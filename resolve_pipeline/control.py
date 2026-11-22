"""Local reel generation and review state shared by dashboard controls."""

from contextlib import closing
from dataclasses import replace
import json
from tempfile import NamedTemporaryFile
from uuid import uuid4
from pathlib import Path
import sqlite3
import time
import subprocess
from threading import Event, Lock, Thread

from urllib.parse import urlsplit

from tools.m0_probe import PROVIDERS, probe_energy

from .dashboard_settings import DEFAULTS, validate_settings
from .downloader import DownloadExhausted, DownloadRequest, Downloader, Registry
from .framing import render_vertical
from .performance import Performance
from .scheduler import FORMATS, choose_format
from .segments import clip_window


class Pipeline:
    def __init__(self, directory, sources, downloader=None, *, reel_builder=None):
        self.sources = tuple(Path(source).resolve() for source in sources)
        if not self.sources:
            raise ValueError("At least one local source is required")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.downloader = downloader if downloader is not None else Downloader(Registry(()), {}, self.directory / "downloads")
        self.reel_builder = reel_builder
        self.database = self.directory / "reels.sqlite3"
        self.render_lock = Lock()
        self.lifecycle = Lock()
        self.stop_signal = Event()
        self.worker = None
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS reels (id INTEGER PRIMARY KEY, "
                       "source TEXT NOT NULL, status TEXT NOT NULL, error TEXT)")

            db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS library (id INTEGER PRIMARY KEY, path TEXT UNIQUE, "
                       "kind TEXT NOT NULL, pinned INTEGER DEFAULT 0, retired INTEGER DEFAULT 0, peak REAL)")
            db.executemany("INSERT OR IGNORE INTO library(path,kind) VALUES (?, 'clip')",
                           ((str(source),) for source in self.sources))

            db.execute("CREATE TABLE IF NOT EXISTS site_overrides (name TEXT PRIMARY KEY, enabled INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS dashboard_events (id INTEGER PRIMARY KEY, "
                       "time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, action TEXT NOT NULL, outcome TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS telegram_reviews (callback_id TEXT PRIMARY KEY, "
                       "reel_id INTEGER NOT NULL, action TEXT NOT NULL, UNIQUE(reel_id,action))")
            db.execute("CREATE TABLE IF NOT EXISTS telegram_previews ("
                       "reel_id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
            existing = {row[1] for row in db.execute("PRAGMA table_info(reels)")}
            for name, definition in (
                ("caption", "TEXT NOT NULL DEFAULT ''"),
                ("hashtags", "TEXT NOT NULL DEFAULT ''"),
                ("posting_order", "INTEGER"),
                ("format_id", "TEXT"),
                ("song_id", "INTEGER"),
            ):
                if name not in existing:
                    db.execute(f"ALTER TABLE reels ADD COLUMN {name} {definition}")

        self.performance = Performance(self.database)

    def sites(self):
        configured = {provider.name: provider.enabled for provider in self.downloader.registry.providers}
        with closing(sqlite3.connect(self.database)) as db:
            overrides = dict(db.execute("SELECT name,enabled FROM site_overrides"))
        return {name: bool(overrides.get(name, configured.get(name, True)))
                for name in sorted(set(PROVIDERS) | set(configured))}

    def toggle_site(self, name):
        configured = {provider.name: provider.enabled for provider in self.downloader.registry.providers}
        if name not in PROVIDERS and name not in configured:
            raise ValueError("Unknown site")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO site_overrides VALUES (?,?) ON CONFLICT(name) DO UPDATE "
                       "SET enabled=1-enabled", (name, int(not configured.get(name, True))))

    def request_site_retest(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        job = {"id": "m0-retest-" + uuid4().hex, "limit": 2, "no_browser": False}
        with NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as out:
            temporary = Path(out.name)
            try:
                json.dump(job, out, indent=2)
                out.write("\n")
                out.close()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

    def log_action(self, action, outcome):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO dashboard_events(action,outcome) VALUES (?,?)", (action, outcome))

    def recent_actions(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT time,action,outcome FROM dashboard_events ORDER BY id DESC LIMIT 50").fetchall()

    def add_song(self, path):
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("Song source must be a file")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT OR IGNORE INTO library(path,kind) VALUES (?, 'song')", (str(path),))

    def library(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT id,path,kind,pinned,retired,peak FROM library ORDER BY kind,id").fetchall()

    def library_action(self, asset_id, action):
        with closing(sqlite3.connect(self.database)) as db, db:
            if action == "pin":
                changed = db.execute("UPDATE library SET pinned=1-pinned WHERE id=? AND retired=0",
                                     (asset_id,)).rowcount
            elif action == "retire":
                changed = db.execute("UPDATE library SET retired=1,pinned=0 WHERE id=? AND retired=0",
                                     (asset_id,)).rowcount
            elif action == "timestamp":
                row = db.execute("SELECT path FROM library WHERE id=? AND retired=0", (asset_id,)).fetchone()
                if row is None:
                    raise ValueError("Asset is unavailable")
                try:
                    energy = probe_energy(Path(row[0]))
                except (OSError, subprocess.SubprocessError) as error:
                    raise ValueError("Timestamp detection failed") from error
                if energy["status"] != "energy_peak":
                    raise ValueError("No audio energy peak found")
                changed = db.execute("UPDATE library SET peak=? WHERE id=? AND retired=0",
                                     (energy["start_seconds"], asset_id)).rowcount
            else:
                raise ValueError("Unknown library action")
            if not changed:
                raise ValueError("Asset is unavailable")

    def settings(self):
        with closing(sqlite3.connect(self.database)) as db:
            return DEFAULTS | dict(db.execute("SELECT name,value FROM settings"))

    def save_settings(self, values):
        validated = validate_settings({key: values[key] for key in DEFAULTS})
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executemany("INSERT INTO settings VALUES (?,?) ON CONFLICT(name) DO UPDATE "
                           "SET value=excluded.value", validated.items())

    def make_from_url(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or any(c.isspace() for c in url)):
            raise ValueError("Enter a public HTTP or HTTPS media URL")
        try:
            with self.render_lock:
                registry = self.downloader.registry
                enabled = self.sites()
                self.downloader.registry = Registry(tuple(
                    replace(provider, enabled=enabled[provider.name]) for provider in registry.providers
                ))
                try:
                    download = self.downloader.download(DownloadRequest(url, "video"))
                finally:
                    self.downloader.registry = registry
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

    def enqueue(self, count, format_id="auto"):
        if type(count) is not int or not 1 <= count <= 20:
            raise ValueError("Choose between 1 and 20 reels")
        if format_id not in ("auto", *FORMATS):
            raise ValueError("Choose Auto, F1, F2 or F3")
        if format_id != "auto" and self.reel_builder is None:
            raise ValueError("Configure the format renderer before selecting F1, F2 or F3")
        weights = tuple(map(int, self.settings()["format_weights"].split(",")))
        observations = self.performance.format_observations()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            assets = db.execute(
                "SELECT id,path,pinned FROM library WHERE kind='clip' AND retired=0 ORDER BY id",
            ).fetchall()
            feedback = self.performance.asset_multipliers("clip")
            assets.sort(key=lambda row: (-row[2], -feedback.get(row[0], 1)))
            if not assets:
                raise ValueError("No active clips available")
            songs = []
            if self.reel_builder is not None:
                songs = db.execute(
                    "SELECT id,pinned FROM library WHERE kind='song' AND retired=0 ORDER BY id",
                ).fetchall()
                feedback = self.performance.asset_multipliers("song")
                songs.sort(key=lambda row: (-row[1], -feedback.get(row[0], 1)))
                if not songs:
                    raise ValueError("Add an active song before rendering")
            ids = []
            for offset in range(count):
                selected = format_id
                if format_id == "auto":
                    selected = choose_format(weights, observations) if songs else None
                song_id = songs[offset % len(songs)][0] if songs else None
                cursor = db.execute(
                    "INSERT INTO reels(source,status,format_id,song_id) VALUES (?, 'queued', ?, ?)",
                    (assets[offset % len(assets)][1], selected, song_id),
                )
                ids.append(cursor.lastrowid)
        return ids

    def make_now(self, count, format_id="auto"):
        with self.render_lock:
            ids = self.enqueue(count, format_id)
            for reel_id in ids:
                self._render(reel_id)
        return ids

    def _render(self, reel_id):
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute("SELECT source,format_id,song_id FROM reels WHERE id=? AND status='queued'",
                             (reel_id,)).fetchone()
            if row is None:
                return
            source, format_id, song_id = row
            error = None
            try:
                if format_id:
                    if self.reel_builder is None:
                        raise RuntimeError("Configured reel renderer is unavailable")
                    song = db.execute("SELECT path FROM library WHERE id=?", (song_id,)).fetchone()
                    clip = db.execute("SELECT id FROM library WHERE path=?", (source,)).fetchone()
                    if song is None or clip is None:
                        raise ValueError("A reel media source is no longer available")
                    self.reel_builder(Path(source), Path(song[0]), self.directory / f"{reel_id}.mp4", format_id)
                    self.performance.register(reel_id, song_id, clip[0], format_id)
                else:
                    self._render_preview(reel_id, source)
            except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as exc:
                error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            with db:
                db.execute("UPDATE reels SET status=?, error=? WHERE id=?",
                           ("failed" if error else "review", error, reel_id))

    def _render_preview(self, reel_id, source):
        duration = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file",
             "-show_entries", "format=duration", "-of", "csv=p=0", source],
            text=True, timeout=20,
        ).strip())
        window = clip_window(timestamp_seconds=0, media_seconds=duration, requested_seconds=22)
        render_vertical(source, self.directory / f"{reel_id}.mp4",
                        start_seconds=window.start_seconds,
                        duration_seconds=window.duration_seconds)

    def review(self, reel_id, status):
        if status not in ("approved", "rejected"):
            raise ValueError("Invalid review decision")
        with closing(sqlite3.connect(self.database)) as db, db:
            changed = db.execute("UPDATE reels SET status=?, posting_order=? WHERE id=? AND status='review'",
                                 (status, time.time_ns() if status == 'approved' else None, reel_id)).rowcount
        if not changed:
            raise ValueError("Reel is no longer awaiting review")

    def claim_review_preview(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,caption FROM reels WHERE (status='review' OR (status='approved' "
                "AND id NOT IN (SELECT reel_id FROM telegram_reviews WHERE action='approve'))) "
                "AND id NOT IN (SELECT reel_id FROM telegram_previews) ORDER BY id LIMIT 1"
            ).fetchone()
            if row is not None:
                db.execute("INSERT INTO telegram_previews VALUES (?, 'in_flight')", (row[0],))
            return row

    def finish_review_preview(self, reel_id, *, sent):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE telegram_previews SET status=? WHERE reel_id=? AND status='in_flight'",
                       ("sent" if sent else "uncertain", reel_id))

    def telegram_review(self, callback_id, reel_id, action):
        """Apply one authorized callback once, even across bot restarts."""
        replacement = None
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT source,status,format_id,song_id FROM reels WHERE id=?", (reel_id,)).fetchone()
            allowed = {
                "approve": {"review", "approved"}, "reject": {"review", "approved"},
                "regenerate": {"review", "rejected", "failed"},
                "muted": {"review", "approved", "posted"},
            }
            if row is None or row[1] not in allowed.get(action, ()):
                return False
            if action == "reject" and db.execute(
                "SELECT 1 FROM telegram_reviews WHERE reel_id=? AND action='approve'", (reel_id,),
            ).fetchone():
                return False
            changed = db.execute(
                "INSERT OR IGNORE INTO telegram_reviews VALUES (?,?,?)", (callback_id, reel_id, action)
            ).rowcount
            if not changed:
                return False
            if action in ("approve", "reject"):
                approved = action == "approve"
                if not approved or row[1] != "approved":
                    db.execute("UPDATE reels SET status=?,posting_order=? WHERE id=?",
                               ("approved" if approved else "rejected",
                                time.time_ns() if approved else None, reel_id))
            if action == "regenerate":
                replacement = db.execute(
                    "INSERT INTO reels(source,status,format_id,song_id) VALUES (?, 'queued', ?, ?)",
                    (row[0], row[2], row[3]),
                ).lastrowid
        if replacement is not None:
            with self.render_lock:
                self._render(replacement)
        return True

    def regenerate(self, reel_id):
        with self.render_lock, closing(sqlite3.connect(self.database)) as db:
            with db:
                row = db.execute("SELECT source,format_id,song_id FROM reels WHERE id=? AND status IN "
                                 "('review','rejected','failed')", (reel_id,)).fetchone()
                if row is None:
                    raise ValueError("Reel cannot be regenerated")
                replacement = db.execute(
                    "INSERT INTO reels(source,status,format_id,song_id) VALUES (?, 'queued', ?, ?)", row,
                ).lastrowid
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
