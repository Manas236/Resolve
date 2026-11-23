"""Persist reel slots and publication attempts across runner restarts."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import random
import sqlite3
from zoneinfo import ZoneInfo

from .posting import Post, PostingError


FORMATS = ("F1", "F2", "F3", "F4")


def choose_format(weights, observations, rng=None):
    """Beta Thompson sampling, with operator weights as relative preferences."""
    rng = rng or random
    scores = {}
    for format_id, weight in zip(FORMATS, weights):
        if weight > 0:
            wins, losses = observations.get(format_id, (0, 0))
            scores[format_id] = weight * rng.betavariate(1 + wins, 1 + losses + (3 if format_id == "F4" else 0))
    if not scores:
        raise ValueError("At least one enabled format is required")
    return max(scores, key=scores.get)


class Scheduler:
    def __init__(self, pipeline, publishers, video_url, *, zone="UTC", refresh=None, alert=None):
        self.pipeline = pipeline
        self.publishers = dict(publishers)
        modes = {publisher.dry_run for publisher in self.publishers.values()}
        if len(modes) > 1:
            raise ValueError("Use the same dry-run mode for all publishers")
        self.mode = "dry" if modes == {True} else "live"
        self.video_url = video_url
        self.refresh = refresh
        self.alert = alert
        self.zone = ZoneInfo(zone)
        with closing(self._db()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scheduler_start (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), started TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS posting_slots (
                    slot TEXT PRIMARY KEY, reel_id INTEGER NOT NULL, mode TEXT NOT NULL,
                    UNIQUE(reel_id,mode));
                CREATE TABLE IF NOT EXISTS deliveries (
                    slot TEXT NOT NULL, platform TEXT NOT NULL, status TEXT NOT NULL, remote_id TEXT,
                    PRIMARY KEY(slot,platform));
            """)

    def _db(self):
        return sqlite3.connect(self.pipeline.database)

    def _reserve(self, instant, day, due, settings):
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO scheduler_start VALUES (1,?)", (instant.isoformat(),))
            started = db.execute("SELECT started FROM scheduler_start").fetchone()[0]
            approval_required = (settings["mode"] == "approval"
                                 or instant < datetime.fromisoformat(started) + timedelta(days=7))
            prefix = f"{self.mode}/{day}/"
            reserved = db.execute("SELECT COUNT(*) FROM posting_slots WHERE slot LIKE ?",
                                  (prefix + "%",)).fetchone()[0]
            for minute in due:
                if reserved >= int(settings["volume"]):
                    break
                slot = prefix + minute
                if db.execute("SELECT 1 FROM posting_slots WHERE slot=?", (slot,)).fetchone():
                    continue
                row = db.execute("""
                    SELECT id FROM reels
                    WHERE production=1 AND ((? AND status='approved' AND EXISTS (
                               SELECT 1 FROM telegram_reviews WHERE reel_id=reels.id AND action='approve'))
                           OR (NOT ? AND status IN ('review','approved')))
                      AND id NOT IN (SELECT reel_id FROM posting_slots WHERE mode=?)
                    ORDER BY posting_order IS NULL, posting_order, id LIMIT 1
                """, (approval_required, approval_required, self.mode)).fetchone()
                if row is None:
                    break
                db.execute("INSERT INTO posting_slots VALUES (?,?,?)", (slot, row[0], self.mode))
                reserved += 1
                db.executemany("INSERT INTO deliveries VALUES (?,?,'ready',NULL)",
                               ((slot, platform) for platform in self.publishers))

    def select_format(self, observations=None, rng=None):
        weights = map(int, self.pipeline.settings()["format_weights"].split(","))
        if observations is None:
            observations = self.pipeline.performance.format_observations()
        return choose_format(weights, observations, rng)

    def run(self, stop, *, interval=30):
        if interval <= 0:
            raise ValueError("The scheduler interval must be positive")
        while not stop.is_set():
            self.tick(datetime.now(timezone.utc))
            stop.wait(interval)

    def _notify_failure(self, summary):
        if self.alert is not None:
            try:
                self.alert(summary)
            except (OSError, RuntimeError, ValueError):
                self.pipeline.log_action("failure alert", "delivery failed")

    def tick(self, now):
        """Attempt due slots. Uncertain outbound calls stay held for reconciliation."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Scheduler requires a timezone-aware datetime")
        settings = self.pipeline.settings()
        if self.refresh is not None:
            for name, result in self.refresh.tick(now, settings["refresh_cadence"]).items():
                self.pipeline.log_action("refresh:" + name, result)
                if result != "completed":
                    self._notify_failure(f"Resolve {name} refresh failed ({result}).")
        if not self.publishers:
            return []
        instant = now.astimezone(timezone.utc)
        local = now.astimezone(self.zone)
        slots = settings["posting_slots"].split(",")[:int(settings["volume"])]
        day, clock = local.date().isoformat(), local.strftime("%H:%M")
        self._reserve(instant, day, [slot for slot in slots if slot <= clock], settings)
        with closing(self._db()) as db:
            ready = db.execute("""
                SELECT d.slot,d.platform,s.reel_id,r.caption,r.hashtags
                FROM deliveries d JOIN posting_slots s USING(slot)
                JOIN reels r ON r.id=s.reel_id WHERE d.status='ready' AND d.slot LIKE ?
                ORDER BY d.slot,d.platform
            """, (f"{self.mode}/{day}/%",)).fetchall()
        events = []
        for slot, platform, reel_id, caption, hashtags in ready:
            if platform not in self.publishers:
                continue
            with closing(self._db()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                mode = db.execute("SELECT value FROM settings WHERE name='mode'").fetchone()
                started = db.execute("SELECT started FROM scheduler_start").fetchone()[0]
                approval_required = (mode != ("auto",)
                                     or instant < datetime.fromisoformat(started) + timedelta(days=7))
                current = db.execute(
                    "SELECT status, EXISTS (SELECT 1 FROM telegram_reviews "
                    "WHERE reel_id=? AND action='approve') FROM reels WHERE id=? AND production=1", (reel_id, reel_id),
                ).fetchone()
                if current is None or current[0] not in {"review", "approved"}:
                    continue
                if approval_required and current != ("approved", 1):
                    continue
                if not db.execute("""
                    UPDATE deliveries SET status='in_flight'
                    WHERE slot=? AND platform=? AND status='ready'
                """, (slot, platform)).rowcount:
                    continue
            try:
                receipt = self.publishers[platform].publish(
                    Post(str(reel_id), self.video_url(reel_id), " ".join(filter(None, (caption, hashtags))), authorized=True))
            except (PostingError, OSError, ValueError):
                with closing(self._db()) as db, db:
                    db.execute("UPDATE deliveries SET status='uncertain' WHERE slot=? AND platform=?",
                               (slot, platform))
                self._notify_failure(f"Resolve {platform} delivery for reel {reel_id} requires reconciliation.")
                events.append((slot, platform, "uncertain"))
                continue
            with closing(self._db()) as db, db:
                status = "dry_run" if receipt.dry_run else "published"
                db.execute("UPDATE deliveries SET status=?,remote_id=? WHERE slot=? AND platform=?",
                           (status, receipt.remote_id, slot, platform))
                remaining = db.execute(
                    "SELECT 1 FROM deliveries WHERE slot=? AND status!='published'", (slot,),
                ).fetchone()
                if remaining is None:
                    db.execute("UPDATE reels SET status='posted' WHERE id=?", (reel_id,))
            events.append((slot, platform, status))
        return events
