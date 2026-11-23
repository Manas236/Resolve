import unittest

from resolve_pipeline import poetry
from resolve_pipeline.caption_style import quiet_ass
from resolve_pipeline.reel_text import normalized


class PoetryTests(unittest.TestCase):
    def test_valid_manuscript_and_reading_time(self):
        pieces = poetry.BANK.read_text().splitlines()
        self.assertGreaterEqual(len(set(pieces)), 150)
        for text in pieces:
            fmt = "P" if len(text.split()) >= 40 else "L"
            self.assertEqual(poetry.validate_piece(text, fmt), text)
            seconds = poetry.reading_seconds(text, fmt)
            self.assertLessEqual(seconds, 35)
            self.assertGreaterEqual(seconds, 20 if fmt == "P" else 10)
            self.assertEqual(quiet_ass(fmt, text, [], [], 20000).count("Dialogue:"), 1)

    def test_best_valid_unused_candidate_wins(self):
        pieces = poetry.BANK.read_text().splitlines()[:5]
        rows = [{"text": text, "scores": dict.fromkeys(poetry.RUBRIC, i)} for i, text in enumerate(pieces)]
        result = poetry.candidate(lambda _: {"candidates": rows}, "L", "", {normalized(pieces[4])}, normalized)
        self.assertEqual(result["hook"], pieces[3])

    def test_mood_prefers_compatible_footage_and_music(self):
        from resolve_pipeline.speech_edit import footage_distance, music_distance, text_mood
        mood = text_mood("He sat until the old clock grew quiet.", "")
        self.assertLess(footage_distance(mood, {"intensity": .25}), footage_distance(mood, {"intensity": .8}))
        self.assertLess(music_distance(mood, {"mood": "calm resolve", "energy": .35}),
                        music_distance(mood, {"mood": "aggressive", "energy": .7}))
