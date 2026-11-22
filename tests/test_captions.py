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


class HighlightedCaptionTests(unittest.TestCase):
    def test_clip_sync_and_literal_text(self):
        from resolve_pipeline.captions import highlighted_ass

        result = highlighted_ass([Cue(900, 1250, "Keep"), Cue(1260, 1480, "{going}")],
                                 start_ms=1000, end_ms=1400, hook="START NOW")
        self.assertIn("Dialogue: 1,0:00:00.00,0:00:00.40,Hook,START NOW", result)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:00.25,Word,Keep", result)
        self.assertIn("Dialogue: 0,0:00:00.26,0:00:00.40,Word,｛going｝", result)

    def test_zero_length_word_is_skipped_without_shifting_neighbours(self):
        from resolve_pipeline.captions import highlighted_ass

        result = highlighted_ass([Cue(0, 300, "First"), Cue(300, 300, "collapsed"),
                                  Cue(300, 600, "Last.")], start_ms=0, end_ms=1000)
        self.assertNotIn("collapsed", result)
        self.assertIn("0:00:00.30,0:00:00.60,Word,Last.", result)

    def test_whisper_adapter_uses_word_timestamps(self):
        from unittest.mock import Mock
        from resolve_pipeline.captions import transcribe_words

        model = Mock()
        model.transcribe.return_value = {"segments": [{"words": [
            {"start": .125, "end": .4, "word": " Start"},
        ]}]}
        self.assertEqual(transcribe_words("clean.wav", model), [Cue(125, 400, "Start")])
        model.transcribe.assert_called_once_with("clean.wav", word_timestamps=True)
