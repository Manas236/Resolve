from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from random import Random
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from resolve_pipeline.control import Pipeline
from resolve_pipeline.posting import Receipt, MetaPublisher
from resolve_pipeline.scheduler import Scheduler, choose_format


class RecordingPublisher:
    dry_run = False

    def __init__(self, crash=False):
        self.requests = []
        self.crash = crash

    def publish(self, post):
        self.requests.append(post)
        if self.crash:
            raise SystemExit("simulate process interruption after dispatch")
        return Receipt("instagram", post.reel_id, False)


class SchedulerTests(unittest.TestCase):
    def test_mode_slots_and_crash_after_dispatch(self):
        with TemporaryDirectory() as directory:
            pipeline = Pipeline(Path(directory), [Path(directory) / "source.mp4"])
            with closing(sqlite3.connect(pipeline.database)) as db, db:
                db.executemany("INSERT INTO reels(source,status,production) VALUES (?, 'review', 1)", [("source",)] * 5)
            pipeline.review(1, "approved")
            pipeline.telegram_review("b", 2, "approve")
            pipeline.telegram_review("c", 3, "approve")
            pipeline.telegram_review("d", 4, "approve")
            pipeline.save_settings(pipeline.settings() | {"mode": "auto"})
            start = datetime(2026, 11, 22, 8, tzinfo=timezone.utc)
            crashing = RecordingPublisher(crash=True)
            get_url = lambda reel_id: f"https://example.org/{reel_id}.mp4"
            self.assertEqual(Scheduler(pipeline, {"instagram": crashing}, get_url).tick(start), [])
            with self.assertRaises(SystemExit):
                Scheduler(pipeline, {"instagram": crashing}, get_url).tick(start + timedelta(hours=1))
            self.assertEqual([request.reel_id for request in crashing.requests], ["2"])

            restarted = Pipeline(Path(directory), [Path(directory) / "source.mp4"])
            real = RecordingPublisher()
            runner = Scheduler(restarted, {"instagram": real}, get_url)
            self.assertEqual(runner.tick(start + timedelta(hours=1)), [])
            self.assertEqual(len(runner.tick(start + timedelta(hours=10))), 1)
            self.assertEqual([request.reel_id for request in real.requests], ["3"])
            self.assertTrue(real.requests[0].authorized)
            self.assertEqual(runner.tick(start + timedelta(hours=11)), [])
            self.assertEqual(len(runner.tick(start + timedelta(days=8, hours=10))), 2)
            self.assertEqual([request.reel_id for request in real.requests], ["3", "1", "4"])
            self.assertEqual(runner.tick(start + timedelta(days=8, hours=11)), [])

    def test_dry_run_does_not_reserve_live_slot(self):
        with TemporaryDirectory() as directory:
            pipeline = Pipeline(Path(directory), [Path(directory) / "source.mp4"])
            with closing(sqlite3.connect(pipeline.database)) as db, db:
                db.execute("INSERT INTO reels(source,status,production) VALUES ('source', 'review', 1)")
            pipeline.telegram_review("approve", 1, "approve")
            now = datetime(2026, 11, 22, 9, tzinfo=timezone.utc)
            dry = MetaPublisher("instagram", version="v24.0")
            get_url = lambda reel_id: f"https://example.org/{reel_id}.mp4"
            self.assertEqual(Scheduler(pipeline, {"instagram": dry}, get_url).tick(now)[0][2], "dry_run")
            live = RecordingPublisher()
            self.assertEqual(Scheduler(pipeline, {"instagram": live}, get_url).tick(now)[0][2], "published")
            self.assertEqual(len(live.requests), 1)

    def test_bandit_explores_and_favors_observed_wins(self):
        rng = Random(17)
        initial = {choose_format((1, 99, 99, 1, 1, 1), {}, rng) for _ in range(200)}
        self.assertEqual(initial, {"F1", "P", "L", "F4"})
        choices = [choose_format((1, 1, 0, 0, 1, 0), {"F1": (20, 0), "P": (0, 20)}, rng)
                   for _ in range(30)]
        self.assertGreater(choices.count("F1"), 27)
