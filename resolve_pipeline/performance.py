"""Comparable view snapshots, mute signals, and measured ranking feedback."""

from contextlib import closing
from statistics import median
import sqlite3

from .scheduler import FORMATS

WINDOW_HOURS = 24
MIN_BASELINE_COUNT = 5
MIN_BASELINE_VIEWS = 100
MUTE_FRACTION = 0.20


class Performance:
    def __init__(self, database):
        self.database = database
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS measured_reels (
                    reel_id INTEGER PRIMARY KEY, song_id INTEGER NOT NULL, clip_id INTEGER NOT NULL,
                    format_id TEXT NOT NULL, views INTEGER CHECK(views>=0))
            """)

    def register(self, reel, song, clip, format_id):
        if format_id not in FORMATS or any(type(value) is not int or value < 1
                                           for value in (reel, song, clip)):
            raise ValueError("Invalid performance dimensions")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT OR IGNORE INTO measured_reels(reel_id,song_id,clip_id,format_id) "
                       "VALUES (?,?,?,?)", (reel, song, clip, format_id))
            dimensions = db.execute(
                "SELECT song_id,clip_id,format_id FROM measured_reels WHERE reel_id=?", (reel,),
            ).fetchone()
            if dimensions != (song, clip, format_id):
                raise ValueError("Performance dimensions are immutable after registration")

    def ingest_views(self, reel, views, *, age_hours):
        """Store a combined view snapshot obtained at the standardized reel age."""
        if type(views) is not int or views < 0 or age_hours != WINDOW_HOURS:
            raise ValueError("Ingest a nonnegative 24-hour view count")
        with closing(sqlite3.connect(self.database)) as db, db:
            changed = db.execute("UPDATE measured_reels SET views=? WHERE reel_id=?",
                                 (views, reel)).rowcount
            if not changed:
                raise ValueError("Register the reel before ingesting views")

    def rows(self):
        with closing(sqlite3.connect(self.database)) as db:
            has_reviews = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_reviews'"
            ).fetchone()
            query = (
                "SELECT reel_id, song_id, clip_id, format_id, views, "
                + ("EXISTS(SELECT 1 FROM telegram_reviews t WHERE t.reel_id=m.reel_id AND t.action='muted')"
                   if has_reviews else "0")
                + " FROM measured_reels m ORDER BY reel_id"
            )
            raw = db.execute(query).fetchall()
        result = []
        for reel, song, clip, format_id, views, manual in raw:
            peers = [row[4] for row in raw if row[3] == format_id and
                     row[0] != reel and row[4] is not None and not row[5]]
            baseline = median(peers) if len(peers) >= MIN_BASELINE_COUNT else None
            inferred = (views is not None and baseline is not None and
                        baseline >= MIN_BASELINE_VIEWS and views < baseline * MUTE_FRACTION)
            result.append({"reel": reel, "song": song, "clip": clip, "format": format_id,
                           "views": views, "baseline": baseline, "manual_muted": bool(manual),
                           "inferred_muted": inferred})
        return result

    def grouped(self, dimension):
        if dimension not in {"reel", "song", "clip", "format"}:
            raise ValueError("Unknown performance dimension")
        groups = {}
        for row in self.rows():
            key = row[dimension]
            group = groups.setdefault(key, {"observed": 0, "views": 0, "muted": 0})
            if row["views"] is not None:
                group["observed"] += 1
                group["views"] += row["views"]
            group["muted"] += row["manual_muted"] or row["inferred_muted"]
        return groups

    def asset_multipliers(self, dimension):
        if dimension not in {"song", "clip"}:
            raise ValueError("Ranking multiplier applies to songs and clips")
        totals = {}
        for row in self.rows():
            counts = totals.setdefault(row[dimension], [2, 0])
            counts[0] += 1
            counts[1] += row["manual_muted"] or row["inferred_muted"]
        return {asset: (count - muted) / count for asset, (count, muted) in totals.items()}

    def asset_multiplier(self, dimension, asset):
        return self.asset_multipliers(dimension).get(asset, 1.0)

    def format_observations(self):
        rows = self.rows()
        measured = [row["views"] for row in rows if row["views"] is not None]
        reference = median(measured) if measured else None
        counts = {format_id: [0, 0] for format_id in FORMATS}
        for row in rows:
            if row["manual_muted"] or row["inferred_muted"]:
                counts[row["format"]][1] += 1
            elif row["views"] is not None:
                counts[row["format"]][int(row["views"] < reference)] += 1
        return {format_id: tuple(count) for format_id, count in counts.items()}
