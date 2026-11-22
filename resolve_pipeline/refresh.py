"""Durable cadence for song, clip and provider health refresh jobs."""

from calendar import monthrange
from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
from uuid import uuid4

JOBS = ("songs", "clips", "site_health")


def next_refresh(last, cadence):
    if cadence == "weekly":
        return last + timedelta(weeks=1)
    if cadence != "monthly":
        raise ValueError("Refresh cadence must be weekly or monthly")
    year, month = (last.year + 1, 1) if last.month == 12 else (last.year, last.month + 1)
    return last.replace(year=year, month=month, day=min(last.day, monthrange(year, month)[1]))


class RefreshJobs:
    """Schedules idempotent refresh callbacks, with stale-claim recovery."""

    def __init__(self, database, *, songs, clips, site_health, lease=timedelta(hours=3)):
        if lease <= timedelta():
            raise ValueError("Refresh lease must be positive")
        self.database = database
        self.callbacks = dict(zip(JOBS, (songs, clips, site_health)))
        self.lease = lease
        with closing(self._db()) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS refresh_jobs (
                    name TEXT PRIMARY KEY, completed TEXT, attempted TEXT,
                    claim TEXT, failure TEXT)
            """)
            db.executemany("INSERT OR IGNORE INTO refresh_jobs(name) VALUES (?)",
                           ((job,) for job in JOBS))

    def _db(self):
        return sqlite3.connect(self.database)

    def tick(self, now, cadence):
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Refresh requires a timezone-aware datetime")
        if cadence not in ("weekly", "monthly"):
            raise ValueError("Refresh cadence must be weekly or monthly")
        now = now.astimezone(timezone.utc)
        results = {}
        for name, callback in self.callbacks.items():
            claim = self._claim(name, now, cadence)
            if claim is None:
                continue
            failure = None
            try:
                callback()
            except Exception as exc:
                failure = type(exc).__name__
            with closing(self._db()) as db, db:
                db.execute("""
                    UPDATE refresh_jobs SET completed=COALESCE(?,completed),failure=?,claim=NULL
                    WHERE name=? AND claim=?
                """, (now.isoformat() if failure is None else None, failure, name, claim))
            results[name] = failure or "completed"
        return results

    def _claim(self, name, now, cadence):
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            completed, attempted, active = db.execute(
                "SELECT completed,attempted,claim FROM refresh_jobs WHERE name=?", (name,),
            ).fetchone()
            if completed and now < next_refresh(datetime.fromisoformat(completed), cadence):
                return None
            retry = self.lease if active else timedelta(hours=1)
            if attempted and now < datetime.fromisoformat(attempted) + retry:
                return None
            claim = uuid4().hex
            db.execute("UPDATE refresh_jobs SET attempted=?,claim=? WHERE name=?",
                       (now.isoformat(), claim, name))
            return claim
