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
