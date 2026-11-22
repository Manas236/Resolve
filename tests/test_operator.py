from contextlib import closing
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from resolve_pipeline.control import Pipeline
from resolve_pipeline.operator import build_services, run_iteration


class OperatorTests(unittest.TestCase):
    def test_disconnected_dry_run_and_explicit_live_gate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = Pipeline(root, [root / "source.mp4"])
            with closing(sqlite3.connect(pipeline.database)) as db, db:
                db.execute("INSERT INTO reels(source,status) VALUES ('source', 'review')")
            pipeline.telegram_review("approved", 1, "approve")
            scheduler, bot = build_services(pipeline, {"META_DRY_RUN": "false"})
            self.assertIsNone(bot)
            now = datetime(2026, 11, 22, 18, tzinfo=timezone.utc)
            logger = logging.getLogger("resolve.operator-test")
            with self.assertLogs(logger, level="INFO") as capture:
                self.assertEqual(run_iteration(scheduler, bot, offset=0, now=now, logger=logger), 0)
            self.assertEqual(len(capture.output), 2)
            self.assertTrue(all("dry_run" in entry for entry in capture.output))
            with self.assertRaisesRegex(ValueError, "requires --live"):
                build_services(pipeline, {}, live=True)
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                build_services(pipeline, {"META_DRY_RUN": "false"}, live=True)
