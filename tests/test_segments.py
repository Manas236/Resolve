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
