import os
import unittest
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

    @unittest.skipUnless(os.getenv("RESOLVE_VERIFY_TEN_ENCODINGS") == "1", "Opt-in ten MP4 encodes")
    def test_ten_encoded_review_files(self):
        import shutil
        import subprocess
        from resolve_pipeline.reels import LocalReelBuilder
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video, audio = root/"fixture.mp4", root/"fixture.wav"
            subprocess.run(["ffmpeg","-loglevel","error","-f","lavfi","-i","color=s=160x284:d=2:r=2",
                            "-threads","1",str(video)], check=True)
            subprocess.run(["ffmpeg","-loglevel","error","-f","lavfi","-i","sine=duration=2",str(audio)],check=True)
            builder = LocalReelBuilder(hook="", generate_captions=True)
            pipeline = Pipeline(root/"dashboard", [], reel_builder=builder)
            with sqlite3.connect(pipeline.database) as db:
                for kind, count, src in (("clip",11,video), ("song",6,audio)):
                    for i in range(count):
                        path = root/f"{i}{src.suffix}"
                        shutil.copy(src,path)
                        db.execute("INSERT INTO library(path,kind) VALUES(?,?)",(str(path),kind))
            bank = {str(i): dict(id=str(i),creator=str(i),speaker=str(i),formats=["L"],
                        text=f"Fixture observation {i}.",theme="fixture",mood="reflective",energy=.4) for i in range(10)}
            with patch("resolve_pipeline.quotes.genre_catalog",return_value=bank):
                for key in bank:
                    pipeline.set_quote_state(key,"active")
                ids = pipeline.make_now(10,"L")
            for reel,state,error in pipeline.list_reels():
                self.assertEqual(state,"review",error)
                self.assertGreater((pipeline.directory/f"{reel}.mp4").stat().st_size,0)
            with sqlite3.connect(pipeline.database) as db:
                rows = db.execute("SELECT clip_id,song_id FROM measured_reels ORDER BY reel_id").fetchall()
            self.assertEqual(len(rows),len(ids))
            self.assertEqual(len({clip for clip,_ in rows}),10)
            for i,(_,song) in enumerate(rows):
                self.assertNotIn(song,[s for _,s in rows[max(0,i-5):i]])
