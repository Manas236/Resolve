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
        night = {"intensity": .32, "scores": {"a lone runner at dusk": .85}}
        sprint = {"intensity": .32, "scores": {"an intense sprint": .85}}
        self.assertLess(footage_distance(mood, night), footage_distance(mood, sprint))
        self.assertLess(music_distance(mood, {"mood": "calm resolve", "energy": .35}),
                        music_distance(mood, {"mood": "aggressive", "energy": .7}))

    def test_two_hundred_reel_window_counts_reels_and_combined_reveals(self):
        import json
        import sqlite3
        from contextlib import closing
        from resolve_pipeline.control import Pipeline
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE reels(id INTEGER PRIMARY KEY, video_text)")
            db.executemany("INSERT INTO reels VALUES(?,?)",
                [(1, json.dumps({"hook": "Expired copy", "lines": []})),
                 (2, json.dumps({"hook": "The opening", "lines": ["its ending."]})),
                 *((i, None) for i in range(3, 202))])
            recent = Pipeline._recent_video_text(db, 999)
        self.assertNotIn("expired copy", recent)
        self.assertIn("the opening its ending", recent)

    def test_migrates_old_weights_and_keeps_retired_formats_manual(self):
        from resolve_pipeline.dashboard_settings import DEFAULTS, validate_settings
        old = validate_settings(DEFAULTS | {"format_weights": "2,10,10,1"})
        self.assertEqual(old["format_weights"], "2,0,0,1,4,4")

    def test_f4_reveals_at_marker_and_speech_follows_measured_cues(self):
        from resolve_pipeline.captions import Cue
        reveal = quiet_ass("F4", "He was late", ["until the clock stopped."], [], 12000, [(6500, 2)])
        self.assertIn("0:00:06.50", reveal)
        cues = [Cue(0, 400, "One"), Cue(400, 1120, "quiet"), Cue(1120, 1560, "morning.")]
        speech = quiet_ass("F1", "", [], cues, 20000)
        self.assertEqual(speech.count("Dialogue:"), 3)
        self.assertIn("0:00:00.40,0:00:01.12", speech)
        self.assertNotIn("MORNING", speech)
