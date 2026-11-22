from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from resolve_pipeline.control import Pipeline
from resolve_pipeline.refresh import RefreshJobs, next_refresh
from resolve_pipeline.scheduler import Scheduler


class RefreshTests(unittest.TestCase):
    def test_each_job_recovers_independently_and_restarts_on_cadence(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "db.sqlite"
            events = []

            def fail():
                events.append("songs")
                raise OSError("network")

            jobs = RefreshJobs(database, songs=fail, clips=lambda: events.append("clips"),
                               site_health=lambda: events.append("sites"))
            now = datetime(2026, 1, 31, tzinfo=timezone.utc)
            self.assertEqual(jobs.tick(now, "monthly"),
                             {"songs": "OSError", "clips": "completed", "site_health": "completed"})
            self.assertEqual(jobs.tick(now, "monthly"), {})
            restarted = RefreshJobs(database, songs=lambda: events.append("songs"),
                                    clips=lambda: events.append("clips"),
                                    site_health=lambda: events.append("sites"))
            self.assertEqual(restarted.tick(now + timedelta(hours=1), "monthly"), {"songs": "completed"})
            self.assertEqual(restarted.tick(now + timedelta(days=27), "monthly"), {})
            self.assertEqual(restarted.tick(now + timedelta(days=28), "monthly"),
                             {"clips": "completed", "site_health": "completed"})
            self.assertEqual(events, ["songs", "clips", "sites", "songs", "clips", "sites"])
            self.assertEqual(next_refresh(now, "weekly"), now + timedelta(weeks=1))

    def test_scheduler_runs_refresh_without_configured_publishers(self):
        with TemporaryDirectory() as directory:
            pipeline = Pipeline(Path(directory), [Path(directory) / "source.mp4"])
            records = []
            refresh = RefreshJobs(pipeline.database, songs=lambda: records.append("songs"),
                                  clips=lambda: records.append("clips"),
                                  site_health=lambda: records.append("site_health"))
            runner = Scheduler(pipeline, {}, None, refresh=refresh)
            now = datetime(2026, 1, 31, tzinfo=timezone.utc)
            self.assertEqual(runner.tick(now), [])
            self.assertEqual(runner.tick(now + timedelta(days=1)), [])
            self.assertEqual(records, ["songs", "clips", "site_health"])
