import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from resolve_pipeline.control import Pipeline


class RotationTest(TestCase):
    def test_ten_consecutive_review_renders(self):
        mood = {"energy": .4, "mood": "reflective"}
        clips = {"intensity": .4, "label": "dark moody gym at night"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            builder = Mock(generate_captions=True, gemini_key=None)
            builder.format_requirements.return_value = {"L": None}
            pipeline = Pipeline(root, [], reel_builder=builder)
            with sqlite3.connect(pipeline.database) as db:
                for kind, count in (("clip",11), ("song",6)):
                    for i in range(count):
                        path = root/f"{kind}{i}"
                        path.touch()
                        db.execute("INSERT INTO library(path,kind) VALUES(?,?)", (str(path),kind))
            bank = {str(i): {"id": str(i), "creator": str(i), "speaker": str(i),
                    "formats":["L"], "text":f"Fixture observation {i}.", "theme":"fixture", **mood}
                    for i in range(10)}
            with patch("resolve_pipeline.quotes.genre_catalog", return_value=bank), patch(
                    "resolve_pipeline.visual_energy.fallback_tags", return_value=clips), patch(
                    "resolve_pipeline.control.song_tags", return_value=mood):
                for key in bank:
                    pipeline.set_quote_state(key, "active")
                pipeline.make_now(10, "L")
            self.assertEqual(builder.call_count, 10, pipeline.list_reels())
            footage = [call.args[0] for call in builder.call_args_list]
            music = [call.args[1] for call in builder.call_args_list]
            self.assertEqual(len(set(footage)),10)
            for i, song in enumerate(music):
                self.assertNotIn(song, music[max(0,i-5):i])
