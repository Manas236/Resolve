import unittest
from unittest.mock import patch
import numpy as np

from resolve_pipeline.captions import Cue
from resolve_pipeline.speech_edit import candidates, relative_words, select_passage


class SpeechEditTests(unittest.TestCase):
    def test_only_complete_sentence_bounded_passages(self):
        words = [Cue(0, 10000, "Start."), Cue(10000, 25000, "Finish."),
                 Cue(25000, 36000, "Again."), Cue(36000, 50000, "unfinished")]
        spans = {(p.clip_start_ms, p.clip_end_ms) for p in candidates(words)}
        self.assertEqual(spans, {(0, 25000), (10000, 36000)})
        with patch("resolve_pipeline.speech_edit.gemini_json", return_value={"index": 1, "mood": "defiant"}):
            passage, meta = select_passage(words, np.ones(50), "fixture", sample_rate=1)
        self.assertIn((passage.clip_start_ms, passage.clip_end_ms), spans)
        self.assertEqual(relative_words(passage)[0].start_ms, 0)
        self.assertEqual(meta["mood"], "defiant")
        with self.assertRaisesRegex(ValueError, "20–35"):
            select_passage(words[:1], np.ones(50), sample_rate=1)

    def test_music_prefers_compatible_mood_and_energy(self):
        from resolve_pipeline.speech_edit import music_distance
        speech = {"mood": "defiant", "energy": 0.7}
        self.assertLess(music_distance(speech, {"mood": "aggressive", "energy": 0.71}),
                        music_distance(speech, {"mood": "calm resolve", "energy": 0.7}))
