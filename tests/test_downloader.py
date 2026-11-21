from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from resolve_pipeline.downloader import (
    AdapterUnavailable, DownloadExhausted, DownloadRequest, Downloader, Media,
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
