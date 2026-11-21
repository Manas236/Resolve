"""Provider-independent download rotation and media validation."""

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import subprocess
from tempfile import NamedTemporaryFile
from typing import BinaryIO, ContextManager, Literal, Protocol

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
        return cls(tuple(
            Provider(
                name=entry["name"], adapter=entry["adapter"], kinds=frozenset(entry["kinds"]),
                enabled=entry.get("enabled", True), priority=entry.get("priority", 0),
            )
            for entry in entries
        ))


class AdapterUnavailable(Exception):
    """A provider declined the attempt or temporarily cannot supply media."""


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
                 *, max_bytes: int = 256 * 1024 * 1024):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.registry = registry
        self.adapters = dict(adapters)
        self.directory = directory
        self.max_bytes = max_bytes
        self._health: dict[tuple[str, MediaKind], float] = {}

    def download(self, request: DownloadRequest) -> Download:
        eligible = [
            provider for provider in self.registry.providers
            if provider.enabled and request.kind in provider.kinds and provider.adapter in self.adapters
        ]
        eligible.sort(key=lambda provider: (-self._health.get((provider.name, request.kind), 0.5),
                                             -provider.priority, provider.name))
        attempts = []
        for provider in eligible:
            health_key = (provider.name, request.kind)
            health = self._health.get(health_key, 0.5)
            try:
                result = self._attempt(provider, request)
            except (AdapterUnavailable, MediaRejected) as error:
                reason = str(error) if isinstance(error, MediaRejected) else "adapter_unavailable"
                attempts.append((provider.name, reason))
                self._health[health_key] = health * 0.75
            else:
                self._health[health_key] = health * 0.75 + 0.25
                return result
        raise DownloadExhausted(attempts)

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
