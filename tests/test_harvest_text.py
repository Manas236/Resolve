from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools.harvest_text import clean, normalized, reserve_call, SCHEMA, Reader


class HarvestTests(unittest.TestCase):
    def test_literal_cleanup(self):
        self.assertEqual(clean("l cansee it .", ["I can see it."]), "I can see it.")
        self.assertEqual(clean("I might see it.", ["I can see it."]), "I might see it.")
        self.assertEqual(normalized("same, Text"), normalized("Same text."))

    def test_cap_and_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"checkpoint"
            db = sqlite3.connect(path)
            db.executescript(SCHEMA)
            self.assertTrue(reserve_call(db, 1, 0))
            db.execute("INSERT INTO videos(id,status) VALUES('done','accepted')")
            db.commit()
            db.close()
            db = sqlite3.connect(path)
            self.assertFalse(reserve_call(db, 1, 0))
            self.assertEqual(db.execute("SELECT count(*) FROM videos WHERE status='pending'").fetchone()[0], 0)
            db.close()

    def test_detection_skip(self):
        reader = Reader(None, SimpleNamespace(), [], "key")
        with patch("tools.harvest_text.detect", return_value=False):
            self.assertEqual(reader.read(None), (None, "no_text_region"))

    def test_image_detector(self):
        from PIL import Image, ImageDraw
        import numpy as np
        from tools.harvest_text import detect
        image = Image.new("RGB", (270,480))
        self.assertFalse(detect(np.asarray(image)))
        draw = ImageDraw.Draw(image)
        for y in (180,200,220):
            draw.text((32,y), "A centered block of text for this fixture.", fill="white")
        self.assertTrue(detect(np.asarray(image)))

    def test_capped_vision_uses_local_reader(self):
        from unittest.mock import Mock
        import sys
        from types import ModuleType
        fake_language = ModuleType("langdetect")
        fake_language.detect = lambda _: "en"
        fake_language.LangDetectException = ValueError
        text = "A short fixture."
        reader = Reader(None, SimpleNamespace(daily=1, interval=5), [text], "dummy")
        reader.ocr = Mock()
        reader.ocr.readtext.return_value = [(None, text, .99)]
        with patch.dict(sys.modules, langdetect=fake_language), patch("tools.harvest_text.detect", return_value=True), patch(
                "tools.harvest_text.reserve_call", return_value=False):
            data, path = reader.read(None)
        self.assertEqual((path, data["text"]), ("easyocr", text))

    def test_restart_after_first_candidate_committed(self):
        from tools import harvest_text as h
        import json
        import os
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as st:
            root = Path(tmp)
            seed = root/"seeds.json"
            seed.write_text("[]")
            bank = root/"candidates.jsonl"
            db = sqlite3.connect(root/"harvest.sqlite3")
            db.executescript(SCHEMA)
            db.execute("INSERT INTO channels(url,priority,done) VALUES('fixture',2,1)")
            db.executemany("INSERT INTO videos(id,channel,meta) VALUES(?,'fixture',?)", [
                (str(i), json.dumps(dict(uploader_id="tester", view_count=5, upload_date="20260101"))) for i in range(2)])
            db.commit()
            db.close()
            st.enter_context(patch.dict(os.environ,RESOLVE_GENRE_BANK=str(bank)))
            st.enter_context(patch("sys.argv", ["harvest","--root",tmp,"--seeds",str(seed)]))
            st.enter_context(patch.object(h,"discover",side_effect=lambda db,target,p:
                db.execute("UPDATE channels SET done=1 WHERE url=?",(target,))))
            st.enter_context(patch.object(h,"images", side_effect=lambda vid,root: [("thumbnail",vid)]))
            read = st.enter_context(patch.object(h.Reader,"read",side_effect=lambda i: (
                dict(text=f"Already recorded fixture {i}.",english=True,confident=True,scores=dict.fromkeys(h.AXES,4)), "fixture")))
            def interrupted(vid, root):
                if vid == "1":
                    raise KeyboardInterrupt()
                return [("thumbnail", vid)]
            with patch.object(h,"images",side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
                h.main()
            with patch.object(h,"images",side_effect=lambda vid,root:[("thumbnail", vid)]):
                h.main()
            self.assertEqual(read.call_count,2)
            self.assertEqual(len(bank.read_text().splitlines()),2)
