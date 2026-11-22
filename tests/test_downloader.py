from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from resolve_pipeline.downloader import (
    AdapterTemporary, AdapterUnavailable, DownloadExhausted, DownloadRequest, Downloader, Media,
    MediaRejected, Provider, Registry, validate_media,
)


class MemoryAdapter:
    def __init__(self, data, calls, name):
        self.data, self.calls, self.name = data, calls, name

    @contextmanager
    def open(self, request):
        self.calls.append(self.name)
        if self.data is None:
            raise AdapterUnavailable("offline")
        with BytesIO(self.data) as stream:
            yield stream


class DownloaderTests(unittest.TestCase):
    def test_invalid_registry_rows_report_the_entry_number(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "registry.json"
            for row in ('null', '{}', '{"name": 42}', '{"name": "site", "adapter": "http", '
                        '"kinds": {"audio": false}}',
                        '{"name": "site", "adapter": "http", "kinds": [null]}'):
                with self.subTest(row=row):
                    path.write_text('[{"name":"valid","adapter":"http","kinds":["video"]},'
                                    + row + ']')
                    with self.assertRaisesRegex(ValueError, "registry entry 2"):
                        Registry.from_json(path)

    def test_registry_and_rotation_by_validated_outcome(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            configuration = root / "providers.json"
            configuration.write_text("""
            [
              {"name":"first","adapter":"offline","kinds":["audio"],"priority":2},
              {"name":"second","adapter":"fake","kinds":["audio"]},
              {"name":"disabled","adapter":"offline","kinds":["audio"],"enabled":false}
            ]
            """, encoding="utf-8")
            calls = []
            downloader = Downloader(Registry.from_json(configuration), {
                "offline": MemoryAdapter(None, calls, "offline"),
                "fake": MemoryAdapter(b"test", calls, "fake"),
            }, root / "media")
            request = DownloadRequest("fixture", "audio", 2)
            with patch("resolve_pipeline.downloader.validate_media", return_value=Media(2)):
                first = downloader.download(request)
                second = downloader.download(request)
            self.assertEqual(calls, ["offline", "fake", "fake"])
            self.assertEqual(first.provider, "second")
            self.assertNotEqual(first.path, second.path)
            self.assertEqual(first.path.read_bytes(), b"test")
            self.assertEqual(list((root / "media").glob("*.partial")), [])

    def test_exhaustion_removes_oversized_and_empty_files(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            providers = Registry(tuple(
                Provider(name, name, frozenset({"audio"})) for name in ("empty", "large")
            ))
            downloader = Downloader(providers, {
                "empty": MemoryAdapter(b"", [], "empty"),
                "large": MemoryAdapter(b"12", [], "large"),
            }, root, max_bytes=1)
            with self.assertRaises(DownloadExhausted) as raised:
                downloader.download(DownloadRequest("fixture", "audio", 2))
            self.assertEqual(raised.exception.attempts, (("empty", "empty_media"), ("large", "size_limit")))
            self.assertEqual(list(root.iterdir()), [])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools unavailable")
    def test_persistent_cache_revalidates_and_health_does_not_replace_cached_media(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / "source.wav"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                 "sine=frequency=440:duration=1", str(audio)], check=True, timeout=10,
            )
            registry = Registry((Provider("source", "source", frozenset({"audio"})),))
            calls = []
            adapters = {"source": MemoryAdapter(audio.read_bytes(), calls, "source")}
            directory = root / "downloads"
            request = DownloadRequest("versioned source", "audio", 1)
            first = Downloader(registry, adapters, directory, persistent=True)
            result = first.download(request)
            second = Downloader(registry, adapters, directory, persistent=True)
            self.assertEqual(second.download(request).path, result.path)
            self.assertEqual(calls, ["source"])
            self.assertEqual(second.health(), {("source", "audio"): 0.625})
            self.assertEqual(second.check_health(request), {"source": "verified"})
            self.assertEqual(list(directory.glob("*.media")), [result.path])
            result.path.write_bytes(b"<html>bad media</html>")
            recovered = second.download(request)
            self.assertNotEqual(recovered.path, result.path)
            self.assertEqual(recovered.path.read_bytes(), audio.read_bytes())
            self.assertEqual(calls, ["source", "source", "source"])

    def test_explicit_transient_failure_retries_but_provider_rejection_does_not(self):
        from resolve_pipeline.retry import retry_read

        with TemporaryDirectory() as directory:
            calls = []
            adapter = MemoryAdapter(b"valid", calls, "temporary")
            request = DownloadRequest("fixture", "audio", 2)
            registry = Registry((Provider("temporary", "temporary", frozenset({"audio"})),))

            @contextmanager
            def occasionally_unavailable(req):
                if not calls:
                    calls.append("temporary-failure")
                    raise AdapterTemporary("temporary provider outage")
                with adapter.open(req) as stream:
                    yield stream

            from types import SimpleNamespace
            downloader = Downloader(registry, {"temporary": SimpleNamespace(open=occasionally_unavailable)},
                                    Path(directory))
            with patch("resolve_pipeline.downloader.validate_media", return_value=Media(2)), \
                 patch("resolve_pipeline.downloader.retry_read",
                       side_effect=lambda operation, **kwargs: retry_read(operation, wait=lambda _: None, **kwargs)):
                self.assertEqual(downloader.download(request).provider, "temporary")
            self.assertEqual(calls, ["temporary-failure", "temporary"])

    def test_duplicate_provider_and_bad_reference_are_rejected(self):
        provider = Provider("valid", "adapter", frozenset({"audio"}))
        with self.assertRaisesRegex(ValueError, "unique"):
            Registry((provider, provider))
        with self.assertRaisesRegex(ValueError, "reference duration"):
            DownloadRequest("fixture", "audio")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools unavailable")
    def test_real_ffprobe_version_validation(self):
        with TemporaryDirectory() as folder:
            audio = Path(folder) / "audio.wav"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                str(audio),
            ], check=True, timeout=10)
            self.assertAlmostEqual(validate_media(audio, DownloadRequest("fixture", "audio", 4)).duration_seconds, 1)
            with self.assertRaisesRegex(MediaRejected, "wrong_duration"):
                validate_media(audio, DownloadRequest("fixture", "audio", 4.1))
            with self.assertRaisesRegex(MediaRejected, "expected_video"):
                validate_media(audio, DownloadRequest("fixture", "video"))
            audio.write_bytes(b"<html>Unexpected landing page</html>")
            with self.assertRaisesRegex(MediaRejected, "invalid_media"):
                validate_media(audio, DownloadRequest("fixture", "audio", 1))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools unavailable")
    def test_real_ffprobe_rejects_low_resolution_and_accepts_720p(self):
        with TemporaryDirectory() as folder:
            request = DownloadRequest("fixture", "video")
            for size in ("640x360", "1280x720"):
                video = Path(folder) / f"{size}.mp4"
                subprocess.run([
                    "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=black:s={size}:r=1:d=1", "-c:v", "mpeg4", str(video),
                ], check=True, timeout=10)
                if size == "640x360":
                    with self.assertRaisesRegex(MediaRejected, "below_720p"):
                        validate_media(video, request)
                else:
                    self.assertEqual(validate_media(video, request), Media(1, 1280, 720))
