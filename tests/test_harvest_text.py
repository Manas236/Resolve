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
