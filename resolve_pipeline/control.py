"""Local reel generation and review state shared by dashboard controls."""

from contextlib import closing
from pathlib import Path
import sqlite3
import time
import subprocess
from threading import Event, Lock, Thread

from urllib.parse import urlsplit

from .downloader import DownloadExhausted, DownloadRequest, Downloader, Registry
from .framing import render_vertical
from .segments import clip_window


class Pipeline:
    def __init__(self, directory, sources, downloader=None):
        self.sources = tuple(Path(source).resolve() for source in sources)
        if not self.sources:
            raise ValueError("At least one local source is required")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.downloader = downloader if downloader is not None else Downloader(Registry(()), {}, self.directory / "downloads")
        self.database = self.directory / "reels.sqlite3"
        self.render_lock = Lock()
        self.lifecycle = Lock()
        self.stop_signal = Event()
        self.worker = None
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS reels (id INTEGER PRIMARY KEY, "
                       "source TEXT NOT NULL, status TEXT NOT NULL, error TEXT)")

            existing = {row[1] for row in db.execute("PRAGMA table_info(reels)")}
            for name, definition in (
                ("caption", "TEXT NOT NULL DEFAULT ''"),
                ("hashtags", "TEXT NOT NULL DEFAULT ''"),
                ("posting_order", "INTEGER"),
            ):
                if name not in existing:
                    db.execute(f"ALTER TABLE reels ADD COLUMN {name} {definition}")

    def make_from_url(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or any(c.isspace() for c in url)):
            raise ValueError("Enter a public HTTP or HTTPS media URL")
        try:
            download = self.downloader.download(DownloadRequest(url, "video"))
        except DownloadExhausted as error:
            raise ValueError("No configured provider could verify video for this URL") from error
        with self.render_lock, closing(sqlite3.connect(self.database)) as db:
            with db:
                reel_id = db.execute("INSERT INTO reels(source,status) VALUES (?, 'queued')",
                                     (str(download.path),)).lastrowid
            self._render(reel_id)
        return reel_id

    def edit_reel(self, reel_id, caption, hashtags):
        if len(caption) > 2200 or len(hashtags) > 500:
            raise ValueError("Caption or hashtags exceed the editing limit")
        with closing(sqlite3.connect(self.database)) as db, db:
            changed = db.execute("UPDATE reels SET caption=?, hashtags=? WHERE id=? "
                                 "AND status IN ('review','approved')",
                                 (caption, hashtags, reel_id)).rowcount
        if not changed:
            raise ValueError("Reel cannot be edited")

    def editorial(self):
        with closing(sqlite3.connect(self.database)) as db:
            return {row[0]: row[1:] for row in db.execute(
                "SELECT id,caption,hashtags FROM reels WHERE status IN ('review','approved')")}

    def posting_queue(self):
        with closing(sqlite3.connect(self.database)) as db:
            return [row[0] for row in db.execute(
                "SELECT id FROM reels WHERE status='approved' ORDER BY COALESCE(posting_order,id),id")]

    def reorder(self, ids):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = {row[0] for row in db.execute("SELECT id FROM reels WHERE status='approved'")}
            if len(ids) != len(current) or set(ids) != current:
                raise ValueError("Include each currently approved reel ID exactly once")
            db.executemany("UPDATE reels SET posting_order=? WHERE id=?",
                           enumerate(ids, start=1))

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive() and not self.stop_signal.is_set()

    def enqueue(self, count):
        if type(count) is not int or not 1 <= count <= 20:
            raise ValueError("Choose between 1 and 20 reels")
        with closing(sqlite3.connect(self.database)) as db, db:
            first = db.execute("INSERT INTO reels(source,status) VALUES (?, 'queued')",
                               (str(self.sources[0]),)).lastrowid
            for offset in range(1, count):
                db.execute("INSERT INTO reels(source,status) VALUES (?, 'queued')",
                           (str(self.sources[offset % len(self.sources)]),))
        return range(first, first + count)

    def make_now(self, count):
        with self.render_lock:
            for reel_id in self.enqueue(count):
                self._render(reel_id)

    def _render(self, reel_id):
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute("SELECT source FROM reels WHERE id=? AND status='queued'",
                             (reel_id,)).fetchone()
            if row is None:
                return
            source = row[0]
            error = None
            try:
                duration = float(subprocess.check_output(
                    ["ffprobe", "-v", "error", "-protocol_whitelist", "file",
                     "-show_entries", "format=duration", "-of", "csv=p=0", source],
                    text=True, timeout=20,
                ).strip())
                window = clip_window(timestamp_seconds=0, media_seconds=duration, requested_seconds=22)
                render_vertical(source, self.directory / f"{reel_id}.mp4",
                                start_seconds=window.start_seconds,
                                duration_seconds=window.duration_seconds)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                error = type(exc).__name__
            with db:
                db.execute("UPDATE reels SET status=?, error=? WHERE id=?",
                           ("failed" if error else "review", error, reel_id))

    def review(self, reel_id, status):
        if status not in ("approved", "rejected"):
            raise ValueError("Invalid review decision")
        with closing(sqlite3.connect(self.database)) as db, db:
            changed = db.execute("UPDATE reels SET status=?, posting_order=? WHERE id=? AND status='review'",
                                 (status, time.time_ns() if status == 'approved' else None, reel_id)).rowcount
        if not changed:
            raise ValueError("Reel is no longer awaiting review")

    def regenerate(self, reel_id):
        with self.render_lock, closing(sqlite3.connect(self.database)) as db:
            with db:
                row = db.execute("SELECT source FROM reels WHERE id=? AND status IN "
                                 "('review','rejected','failed')", (reel_id,)).fetchone()
                if row is None:
                    raise ValueError("Reel cannot be regenerated")
                replacement = db.execute("INSERT INTO reels(source,status) VALUES (?, 'queued')",
                                         row).lastrowid
            self._render(replacement)

    def delete(self, reel_id):
        with self.render_lock, closing(sqlite3.connect(self.database)) as db, db:
            changed = db.execute("UPDATE reels SET status='deleted' WHERE id=? AND "
                                 "status IN ('review','rejected','failed','queued')", (reel_id,)).rowcount
            if not changed:
                raise ValueError("Reel cannot be deleted")

    def list_reels(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT id,status,error FROM reels WHERE status != 'deleted' ORDER BY id DESC").fetchall()

    def _loop(self):
        while not self.stop_signal.is_set():
            with self.render_lock:
                with closing(sqlite3.connect(self.database)) as db:
                    row = db.execute("SELECT id FROM reels WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                if row and not self.stop_signal.is_set():
                    self._render(row[0])
            if not row:
                self.stop_signal.wait(0.25)

    def start(self):
        with self.lifecycle:
            if self.worker is not None and self.worker.is_alive():
                if self.stop_signal.is_set():
                    raise ValueError("Runner is finishing its current reel")
                return
            self.stop_signal.clear()
            self.worker = Thread(target=self._loop, daemon=True)
            self.worker.start()

    def stop(self):
        with self.lifecycle:
            self.stop_signal.set()
