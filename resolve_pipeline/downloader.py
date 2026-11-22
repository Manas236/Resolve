"""Provider-independent download rotation and media validation."""

from collections.abc import Mapping
from contextlib import closing
from hashlib import sha256
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
from tempfile import NamedTemporaryFile
from typing import BinaryIO, ContextManager, Literal, Protocol

from .retry import retry_read

MediaKind = Literal["audio", "video"]
CONTAINERS = frozenset({"mp3", "mov", "mp4", "webm", "matroska", "wav", "ogg", "flac"})


@dataclass(frozen=True)
class DownloadRequest:
    source: str
    kind: MediaKind
    reference_seconds: float | None = None

    def __post_init__(self):
        if not self.source.strip() or self.kind not in ("audio", "video"):
            raise ValueError("A source and a valid media kind are required")
        if self.kind == "audio" and self.reference_seconds is None:
            raise ValueError("Audio downloads require a reference duration")
        if self.reference_seconds is not None and (
            not math.isfinite(self.reference_seconds) or self.reference_seconds <= 0
        ):
            raise ValueError("The reference duration must be positive and finite")


@dataclass(frozen=True)
class Provider:
    name: str
    adapter: str
    kinds: frozenset[MediaKind]
    enabled: bool = True
    priority: int = 0

    def __post_init__(self):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.name) or not self.adapter:
            raise ValueError("Provider name or adapter key is invalid")
        if not self.kinds or not self.kinds <= {"audio", "video"}:
            raise ValueError("Providers must support audio, video, or both")
        if type(self.enabled) is not bool or type(self.priority) is not int:
            raise ValueError("Provider enabled and priority must be boolean and integer")


@dataclass(frozen=True)
class Registry:
    providers: tuple[Provider, ...]

    def __post_init__(self):
        if len({provider.name for provider in self.providers}) != len(self.providers):
            raise ValueError("Provider names must be unique")

    @classmethod
    def from_json(cls, path: Path) -> "Registry":
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("The provider registry must be a JSON array")
        providers = []
        for index, entry in enumerate(entries, start=1):
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get("name"), str)
                    or not isinstance(entry.get("adapter"), str)
                    or not isinstance(entry.get("kinds"), list)
                    or not all(isinstance(kind, str) for kind in entry["kinds"])):
                raise ValueError(f"Invalid provider registry entry {index}")
            providers.append(Provider(
                name=entry["name"], adapter=entry["adapter"], kinds=frozenset(entry["kinds"]),
                enabled=entry.get("enabled", True), priority=entry.get("priority", 0),
            ))
        return cls(tuple(providers))


class AdapterUnavailable(Exception):
    """A provider declined the attempt or temporarily cannot supply media."""


class AdapterTemporary(AdapterUnavailable):
    """A provider explicitly classified its failure as transient and safe to retry."""


class Adapter(Protocol):
    def open(self, request: DownloadRequest) -> ContextManager[BinaryIO]:
        """Open a bounded-time raw media stream or raise AdapterUnavailable."""
        ...


class MediaRejected(ValueError):
    """The file was empty, malformed, or outside the expected media specification."""


@dataclass(frozen=True)
class Media:
    duration_seconds: float
    width: int | None = None
    height: int | None = None


def validate_media(path: Path, request: DownloadRequest, *, ffprobe: str = "ffprobe") -> Media:
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-protocol_whitelist", "file",
                "-format_whitelist", ",".join(sorted(CONTAINERS)),
                "-show_entries",
                "format=duration,format_name:stream=codec_type,width,height:stream_disposition=attached_pic",
                "-of", "json", str(path.absolute()),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError("ffprobe is required for download validation") from error
    except subprocess.TimeoutExpired as error:
        raise MediaRejected("ffprobe_timeout") from error
    if result.returncode:
        raise MediaRejected("invalid_media")
    try:
        data = json.loads(result.stdout)
        streams = data["streams"]
        container = data["format"]
        duration = float(container["duration"])
        formats = set(container["format_name"].split(","))
        codec_types = {stream["codec_type"] for stream in streams}
        dimensions = [
            (int(stream["width"]), int(stream["height"]))
            for stream in streams if stream["codec_type"] == "video"
            and not stream.get("disposition", {}).get("attached_pic")
        ]
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise MediaRejected("invalid_metadata") from error
    if not math.isfinite(duration) or duration <= 0:
        raise MediaRejected("invalid_duration")
    if not CONTAINERS.intersection(formats) or not codec_types <= {"audio", "video"}:
        raise MediaRejected("unsupported_streams")
    expected = request.reference_seconds
    if expected is not None and abs(duration - expected) > 3:
        raise MediaRejected("wrong_duration")
    if request.kind == "audio":
        if "audio" not in codec_types or dimensions:
            raise MediaRejected("expected_audio")
        return Media(duration)
    if not dimensions:
        raise MediaRejected("expected_video")
    width, height = max(dimensions, key=min)
    if min(width, height) < 720:
        raise MediaRejected("below_720p")
    return Media(duration, width, height)


@dataclass(frozen=True)
class Download:
    path: Path
    provider: str
    media: Media


class DownloadExhausted(Exception):
    def __init__(self, attempts: list[tuple[str, str]]):
        self.attempts = tuple(attempts)
        super().__init__("No eligible provider verified the download")


class Downloader:
    """Try enabled, configured adapters in priority and recent-health order."""

    def __init__(self, registry: Registry, adapters: Mapping[str, Adapter], directory: Path,
                 *, max_bytes: int = 256 * 1024 * 1024, persistent=False):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.registry = registry
        self.adapters = dict(adapters)
        self.directory = directory
        self.max_bytes = max_bytes
        self._health: dict[tuple[str, MediaKind], float] = {}
        self.database = directory / "downloads.sqlite3" if persistent else None
        if self.database is not None:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with closing(sqlite3.connect(self.database)) as db, db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS provider_health (
                        provider TEXT, kind TEXT, score REAL NOT NULL, PRIMARY KEY (provider,kind));
                    CREATE TABLE IF NOT EXISTS cache (
                        request TEXT PRIMARY KEY, provider TEXT NOT NULL, filename TEXT NOT NULL);
                """)

    def _eligible(self, request):
        return [
            provider for provider in self.registry.providers
            if provider.enabled and request.kind in provider.kinds and provider.adapter in self.adapters
        ]

    def health(self):
        if self.database is None:
            return dict(self._health)
        with closing(sqlite3.connect(self.database)) as db:
            return {(name, kind): score for name, kind, score in
                    db.execute("SELECT provider,kind,score FROM provider_health")}

    def _update_health(self, provider, kind, success):
        key = (provider.name, kind)
        gain = 0.25 * success
        if self.database is None:
            self._health[key] = self._health.get(key, 0.5) * 0.75 + gain
            return
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("""
                INSERT INTO provider_health VALUES (?,?,?)
                ON CONFLICT(provider,kind) DO UPDATE SET score=0.75*score+?
            """, (*key, 0.375 + gain, gain))

    @staticmethod
    def _cache_key(request):
        identity = json.dumps((request.source, request.kind, request.reference_seconds))
        return sha256(identity.encode()).hexdigest()

    def _cached(self, request, eligible):
        if self.database is None:
            return None
        key = self._cache_key(request)
        with closing(sqlite3.connect(self.database)) as db:
            item = db.execute("SELECT provider,filename FROM cache WHERE request=?", (key,)).fetchone()
        if item is None or item[0] not in {provider.name for provider in eligible}:
            return None
        provider, filename = item
        path = self.directory / filename
        if (re.fullmatch(r"download-[a-z0-9_]+[.]media", filename) and not path.is_symlink()
                and path.is_file() and 0 < path.stat().st_size <= self.max_bytes):
            try:
                media = validate_media(path, request)
            except MediaRejected:
                pass
            else:
                return Download(path, provider, media)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("DELETE FROM cache WHERE request=? AND filename=?", (key, filename))
        return None

    def _retrieve(self, provider, request):
        try:
            result = retry_read(lambda: self._attempt(provider, request), transient=AdapterTemporary)
        except (AdapterUnavailable, MediaRejected) as error:
            self._update_health(provider, request.kind, False)
            return None, str(error) if isinstance(error, MediaRejected) else "adapter_unavailable"
        self._update_health(provider, request.kind, True)
        return result, "verified"

    def download(self, request: DownloadRequest) -> Download:
        eligible = self._eligible(request)
        cached = self._cached(request, eligible)
        if cached:
            return cached
        health = self.health()
        eligible.sort(key=lambda provider: (-health.get((provider.name, request.kind), 0.5),
                                             -provider.priority, provider.name))
        attempts = []
        for provider in eligible:
            result, outcome = self._retrieve(provider, request)
            if result:
                if self.database is not None:
                    with closing(sqlite3.connect(self.database)) as db, db:
                        db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)",
                                   (self._cache_key(request), provider.name, result.path.name))
                return result
            attempts.append((provider.name, outcome))
        raise DownloadExhausted(attempts)

    def check_health(self, request):
        """Measure each configured provider directly with a versioned target."""
        scores = {}
        for provider in self._eligible(request):
            result, outcome = self._retrieve(provider, request)
            if result is not None:
                result.path.unlink(missing_ok=True)
            scores[provider.name] = outcome
        return scores

    def _attempt(self, provider: Provider, request: DownloadRequest) -> Download:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with NamedTemporaryFile(dir=self.directory, prefix="download-", suffix=".partial", delete=False) as output:
            staging = Path(output.name)
            try:
                with self.adapters[provider.adapter].open(request) as stream:
                    size = 0
                    while block := stream.read(64 * 1024):
                        size += len(block)
                        if size > self.max_bytes:
                            raise MediaRejected("size_limit")
                        output.write(block)
                output.close()
                if not size:
                    raise MediaRejected("empty_media")
                media = validate_media(staging, request)
                result = staging.with_suffix(".media")
                staging.replace(result)
                return Download(result, provider.name, media)
            finally:
                staging.unlink(missing_ok=True)
