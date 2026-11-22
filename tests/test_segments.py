import unittest

from resolve_pipeline.segments import ClipWindow, clip_window


class ClipWindowTests(unittest.TestCase):
    def test_keeps_lead_in_in_the_middle(self):
        self.assertEqual(clip_window(timestamp_seconds=50, media_seconds=100,
                                     requested_seconds=20, lead_in_seconds=3), ClipWindow(47, 20))

    def test_clamps_both_edges(self):
        for timestamp, start in ((1, 0), (99, 80)):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(clip_window(timestamp_seconds=timestamp, media_seconds=100,
                                             requested_seconds=20, lead_in_seconds=3), ClipWindow(start, 20))

    def test_short_media_and_invalid_timestamps(self):
        self.assertEqual(clip_window(timestamp_seconds=2, media_seconds=5,
                                     requested_seconds=20), ClipWindow(0, 5))
        for timestamp in (-1, 5, float("nan")):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                clip_window(timestamp_seconds=timestamp, media_seconds=5, requested_seconds=20)


class RealSegmentTests(unittest.TestCase):
    def test_ffmpeg_cut_and_wrong_version_leave_published_segment_intact(self):
        from pathlib import Path
        from shutil import which
        import subprocess
        from tempfile import TemporaryDirectory

        from resolve_pipeline.downloader import DownloadRequest, MediaRejected
        from resolve_pipeline.segments import cut_verified_segment

        if not which("ffmpeg") or not which("ffprobe"):
            self.skipTest("FFmpeg and ffprobe required")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "sample.wav", root / "clip.mp3"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                 "sine=frequency=440:duration=5", str(source)], check=True, timeout=10,
            )
            cut, verified = cut_verified_segment(
                source, target, DownloadRequest("audio", "audio", 5),
                timestamp_seconds=4, duration_seconds=2, lead_in_seconds=1,
            )
            self.assertEqual(cut.start_seconds, 3)
            self.assertAlmostEqual(verified.duration_seconds, 2, places=1)
            original = target.read_bytes()
            with self.assertRaisesRegex(MediaRejected, "wrong_duration"):
                cut_verified_segment(source, target, DownloadRequest("audio", "audio", 9),
                                     timestamp_seconds=4, duration_seconds=2)
            self.assertEqual(target.read_bytes(), original)
