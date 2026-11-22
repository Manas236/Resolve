from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from resolve_pipeline.control import Pipeline
from resolve_pipeline.performance import Performance


class PerformanceTests(unittest.TestCase):
    def test_measured_baseline_manual_signal_and_ranking_feedback(self):
        with TemporaryDirectory() as directory:
            pipeline = Pipeline(Path(directory), [Path(directory) / "source.mp4"])
            tracking = Performance(pipeline.database)
            for reel in range(1, 9):
                tracking.register(reel, 10 if reel in (1, 7, 8) else 20, reel,
                                  "F2" if reel == 8 else "F1")
            for reel in range(1, 7):
                tracking.ingest_views(reel, 1200, age_hours=24)
            tracking.ingest_views(7, 120, age_hours=24)
            tracking.ingest_views(8, 2500, age_hours=24)
            with closing(sqlite3.connect(pipeline.database)) as db, db:
                db.execute("INSERT INTO reels(id,source,status) VALUES (8,'source','posted')")
            self.assertTrue(pipeline.telegram_review("reported", 8, "muted"))
            entries = {row["reel"]: row for row in tracking.rows()}
            self.assertEqual(entries[7]["baseline"], 1200)
            self.assertTrue(entries[7]["inferred_muted"])
            self.assertFalse(entries[7]["manual_muted"])
            self.assertTrue(entries[8]["manual_muted"])
            self.assertFalse(entries[8]["inferred_muted"])
            self.assertLess(tracking.asset_multiplier("song", 10), tracking.asset_multiplier("song", 20))
            self.assertLess(tracking.asset_multiplier("clip", 7), tracking.asset_multiplier("clip", 1))
            self.assertEqual(tracking.format_observations()["F1"], (6, 1))
            self.assertEqual(tracking.format_observations()["F2"], (0, 1))
            self.assertEqual(tracking.grouped("song")[10], {"observed": 3, "views": 3820, "muted": 2})
            with self.assertRaises(ValueError):
                tracking.ingest_views(7, 0, age_hours=2)
            with self.assertRaises(ValueError):
                tracking.register(7, 15, 7, "F1")
            self.assertEqual(tracking.grouped("reel")[7]["views"], 120)
