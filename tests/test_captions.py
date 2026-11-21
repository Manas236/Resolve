import unittest

from resolve_pipeline.captions import Cue, clip_srt


class CaptionTests(unittest.TestCase):
    def test_clamps_and_rebases_to_clip(self):
        cues = [Cue(3000, 5000, "Last"), Cue(0, 1000, "Outside"),
                Cue(900, 1500, "First\nline")]
        self.assertEqual(
            clip_srt(cues, start_ms=1000, end_ms=4000),
            "1\n00:00:00,000 --> 00:00:00,500\nFirst line\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\nLast\n",
        )

    def test_empty_and_invalid_ranges(self):
        self.assertEqual(clip_srt([], start_ms=0, end_ms=1000), "")
        with self.assertRaises(ValueError):
            clip_srt([Cue(100, 90, "Invalid")], start_ms=0, end_ms=1000)
