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
from .bundles import schema
from .reels import NoSpeech
from .speech_edit import music_distance, song_tags
from .reel_text import checked_text, choose_text, normalized
from . import quotes
from .framing import render_vertical
from .performance import Performance
from .scheduler import FORMATS, choose_format
from .segments import clip_window


class Pipeline:
    def __init__(self, directory, sources, downloader=None, *, reel_builder=None):
        self.sources = tuple(Path(source).resolve() for source in sources)
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.downloader = downloader if downloader is not None else Downloader(Registry(()), {}, self.directory / "downloads")
        self.reel_builder = reel_builder
        self.database = self.directory / "reels.sqlite3"
        self.render_lock = Lock()
        self.lifecycle = Lock()
        self.stop_signal = Event()
        self.worker = None
        self.rendering_id = None
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
            db.execute("CREATE TABLE IF NOT EXISTS quote_state (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
            schema(db)
            db.execute("CREATE TABLE IF NOT EXISTS clip_moods(path TEXT PRIMARY KEY, fingerprint TEXT, tags TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS song_moods(path TEXT PRIMARY KEY, fingerprint TEXT, tags TEXT)")
            existing = {row[1] for row in db.execute("PRAGMA table_info(reels)")}
            for name, definition in (
                ("caption", "TEXT NOT NULL DEFAULT ''"),
                ("hashtags", "TEXT NOT NULL DEFAULT ''"),
                ("posting_order", "INTEGER"),
                ("format_id", "TEXT"),
                ("song_id", "INTEGER"),
                ("production", "INTEGER NOT NULL DEFAULT 0"),
                ("render_claim", "INTEGER"),
                ("video_text", "TEXT"),
                ("music_decision", "TEXT"),
            ):
                if name not in existing:
                    db.execute(f"ALTER TABLE reels ADD COLUMN {name} {definition}")

        self.performance = Performance(self.database)

    def quote_states(self):
        with closing(sqlite3.connect(self.database)) as db:
            return dict(db.execute("SELECT id,state FROM quote_state"))

    def set_quote_state(self, identifier, state):
        if identifier not in quotes.catalog() or state not in {"pinned", "retired", "active"}:
            raise ValueError("Unknown quote or state.")
        with self.render_lock, closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT OR REPLACE INTO quote_state VALUES (?,?)", (identifier, state))

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
                out.close()
                temporary.unlink(missing_ok=True)

    def log_action(self, action, outcome):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO dashboard_events(action,outcome) VALUES (?,?)", (action, outcome))

    def recent_actions(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT time,action,outcome FROM dashboard_events ORDER BY id DESC LIMIT 50").fetchall()

    def add_song(self, path):
        self._add_audio(path, "song")

    def add_speech(self, path):
        self._add_audio(path, "speech")

    def _add_audio(self, path, kind):
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("Audio source must be a file")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT OR IGNORE INTO library(path,kind) VALUES (?,?)", (str(path), kind))

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
            values = DEFAULTS | dict(db.execute("SELECT name,value FROM settings"))
        return validate_settings(values)

    def save_settings(self, values):
        current = self.settings()
        validated = validate_settings({key: values.get(key, current[key]) for key in DEFAULTS})
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

    def video_texts(self):
        with closing(sqlite3.connect(self.database)) as db:
            return {reel_id: json.loads(text) for reel_id, text in db.execute(
                "SELECT id,video_text FROM reels WHERE status='review' AND video_text IS NOT NULL")}

    def edit_video_text(self, reel_id, hook, lines):
        with self.render_lock, closing(sqlite3.connect(self.database)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT source,format_id,song_id,caption,hashtags FROM reels "
                                 "WHERE id=? AND status='review' AND video_text IS NOT NULL",
                                 (reel_id,)).fetchone()
                if row is None:
                    raise ValueError("Only reels awaiting review can have their video text changed.")
                if row[1] in {"P", "L", "F4"}:
                    raise ValueError("Published quotations cannot be edited.")
                text = checked_text(hook, lines, row[1]) | {"source": "edited"}
                db.execute("UPDATE reels SET status='rejected' WHERE id=?", (reel_id,))
                replacement = db.execute(
                    "INSERT INTO reels(source,format_id,song_id,caption,hashtags,video_text,status) "
                    "VALUES(?,?,?,?,?,?,'queued')", (*row, json.dumps(text))).lastrowid
            self._render(replacement)
        return replacement

    @staticmethod
    def _recent_video_text(db, reel_id):
        used = {}
        for identifier, raw in db.execute("SELECT id, video_text FROM (SELECT id, video_text FROM reels WHERE id!=? "
                                 "ORDER BY id DESC LIMIT 200) WHERE video_text IS NOT NULL", (reel_id,)):
            text = json.loads(raw)
            blocks = [text["hook"], *text["lines"]]
            for line in [*blocks, " ".join(blocks)]:
                key = normalized(line)
                used[key] = max(identifier, used.get(key, 0))
        return used

    def _reserve_video_text(self, db, reel_id, format_id, context):
        if format_id in {"P", "L", "F4"}:
            with db:
                db.execute("BEGIN IMMEDIATE")
                history = [json.loads(raw or "{}") for raw, in db.execute(
                    "SELECT video_text FROM reels WHERE id!=? ORDER BY id DESC LIMIT 200", (reel_id,))]
                text = quotes.select(format_id, {q.get("id") for q in history},
                                     {q.get("speaker") for q in history[:5]},
                                     dict(db.execute("SELECT id,state FROM quote_state")))
                db.execute("UPDATE reels SET video_text=? WHERE id=?", (json.dumps(text), reel_id))
            return text
        hook = getattr(self.reel_builder, "hook", "")
        quote = getattr(self.reel_builder, "quote", "")
        hook = hook if isinstance(hook, str) else ""
        quote = quote if isinstance(quote, str) and format_id == "F2" else ""
        if format_id in {"P", "L", "F4"}:
            hook = quote = ""
        if quote and len(quote.splitlines()) == 1:
            raise ValueError("--quote needs two to four short lines separated by newlines.")
        with db:
            db.execute("BEGIN IMMEDIATE")
            text = choose_text(format_id, self._recent_video_text(db, reel_id), context=context)
            if hook or quote:
                text = checked_text(hook or text["hook"], quote.splitlines() if quote else text["lines"],
                                    format_id) | {"source": "override"}
            db.execute("UPDATE reels SET video_text=? WHERE id=?", (json.dumps(text), reel_id))
        return text

    def posting_queue(self):
        with closing(sqlite3.connect(self.database)) as db:
            return [row[0] for row in db.execute(
                "SELECT id FROM reels WHERE status='approved' AND production=1 ORDER BY COALESCE(posting_order,id),id")]

    def reorder(self, ids):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = {row[0] for row in db.execute("SELECT id FROM reels WHERE status='approved' AND production=1")}
            if len(ids) != len(current) or set(ids) != current:
                raise ValueError("Include each currently approved reel ID exactly once")
            db.executemany("UPDATE reels SET posting_order=? WHERE id=?",
                           enumerate(ids, start=1))

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive() and not self.stop_signal.is_set()

    def format_requirements(self):
        if hasattr(self.reel_builder, "format_requirements"):
            readiness = self.reel_builder.format_requirements()
            result = {fmt: readiness.get(fmt, "Renderer does not support this format.") for fmt in FORMATS}
            if result["F1"] is None and not any(row[2] == "speech" and not row[4] for row in self.library()):
                result["F1"] = "Add a separate speech source."
            return result
        return {item: None if self.reel_builder else "Configure the format renderer." for item in FORMATS}

    def enqueue(self, count, format_id="auto", *, production_only=False, production_day=None):
        if type(count) is not int or not 1 <= count <= 20:
            raise ValueError("Choose between 1 and 20 reels")
        if format_id not in ("auto", *FORMATS):
            raise ValueError("Choose Auto, P, L, F1, F4, or a legacy format")
        if format_id != "auto" and self.reel_builder is None:
            raise ValueError("Configure the format renderer before selecting a format")
        weights = tuple(map(int, self.settings()["format_weights"].split(",")))
        requirements = self.format_requirements()
        if format_id != "auto" and requirements[format_id]:
            raise ValueError(requirements[format_id])
        weights = tuple(weight if not requirements[item] else 0 for item, weight in zip(FORMATS, weights))
        if self.reel_builder and format_id == "auto" and not any(weights):
            raise ValueError("No enabled format has its dependencies configured.")
        observations = self.performance.format_observations()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            assets = db.execute(
                "SELECT id,path,pinned FROM library WHERE kind='clip' AND retired=0 ORDER BY id",
            ).fetchall()
            feedback = self.performance.asset_multipliers("clip")
            trusted = {row[0] for row in db.execute("SELECT path FROM imported_media WHERE kind='clip'")}
            assets.sort(key=lambda row: (row[1] not in trusted, -row[2], -feedback.get(row[0], 1)))
            if production_only:
                assets = [row for row in assets if row[1] in trusted]
            if not assets:
                raise ValueError("No active clips available")
            songs = []
            if self.reel_builder is not None:
                songs = db.execute(
                    "SELECT id,pinned,path FROM library WHERE kind='song' AND retired=0 ORDER BY id",
                ).fetchall()
                feedback = self.performance.asset_multipliers("song")
                music = {path: json.loads(meta) for path, meta in db.execute(
                    "SELECT path,metadata FROM imported_media WHERE kind='song'")}
                songs.sort(key=lambda row: (row[2] not in music,
                    music.get(row[2], {}).get("genre") not in {"phonk", "gym_edit"},
                    -row[1], -feedback.get(row[0], 1),
                    -music.get(row[2], {}).get("source_appearances", 0)))
                if production_only:
                    songs = [row for row in songs if row[2] in music]
                if not songs:
                    raise ValueError("Add an active song before rendering")
            if production_only and not db.execute(
                    "SELECT 1 FROM library JOIN imported_media USING(path) WHERE library.kind='speech' "
                    "AND imported_media.kind='speech' AND retired=0 LIMIT 1").fetchone():
                weights = (0, *weights[1:])
                if not any(weights):
                    raise ValueError("Import a speech source or enable another available production format.")
            if production_day:
                db.execute("CREATE TABLE IF NOT EXISTS daily_render(day TEXT PRIMARY KEY)")
                if not db.execute("INSERT OR IGNORE INTO daily_render VALUES(?)",
                                  (production_day,)).rowcount:
                    return []
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

    def music_decisions(self):
        with closing(sqlite3.connect(self.database)) as db:
            return {key: json.loads(raw) for key, raw in db.execute(
                "SELECT id,music_decision FROM reels WHERE music_decision IS NOT NULL")}

    def _match_footage(self, db, mood, production, current):
        from .speech_edit import footage_distance
        options = []
        for identifier, name, raw in db.execute("""SELECT library.id,library.path,metadata FROM library
                LEFT JOIN imported_media USING(path) WHERE library.kind='clip' AND retired=0
                AND (?=0 OR metadata IS NOT NULL)""", (int(production),)):
            meta = json.loads(raw) if raw else {}
            tags = meta.get("vision", {}).get("mood", {})
            if not tags:
                from .visual_energy import fallback_tags
                try:
                    stat = Path(name).stat()
                    start = segment_starts([meta])[0] if meta else 0
                    fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}:{start}"
                    cached = db.execute("SELECT tags FROM clip_moods WHERE path=? AND fingerprint=?",
                                        (name, fingerprint)).fetchone()
                    tags = json.loads(cached[0]) if cached else fallback_tags(name, start)
                    if not cached:
                        with db:
                            db.execute("INSERT OR REPLACE INTO clip_moods VALUES(?,?,?)",
                                       (name, fingerprint, json.dumps(tags)))
                except (OSError, ValueError, subprocess.SubprocessError):
                    continue
            options.append((footage_distance(mood, tags), name != current, identifier, name, meta, tags))
        if not options:
            raise ValueError("No active training clip has readable frames.")
        distance, _, identifier, name, meta, tags = min(options)
        reason = (f"{tags['label']}, visual intensity {tags['intensity']:.2f} versus text energy {mood['energy']:.2f}."
                  if tags else "No clip in this library has mood tags yet; retained the queued training clip.")
        return identifier, Path(name), segment_starts([meta])[0] if meta else 0, reason

    def _match_song(self, db, reel_id, speech, production):
        rows = db.execute("""SELECT library.id,library.path,imported_media.metadata FROM library
                             LEFT JOIN imported_media USING(path)
                             WHERE library.kind='song' AND retired=0 AND (?=0 OR metadata IS NOT NULL)
                             ORDER BY pinned DESC,id""", (int(production),)).fetchall()
        matches = []
        for asset_id, name, raw in rows:
            path = Path(name)
            metadata = json.loads(raw) if raw else {}
            title = metadata.get("title") or path.stem
            try:
                stat = path.stat()
                key = self.reel_builder.gemini_key
                fingerprint = f"poetic:{metadata.get('genre', '')}:{stat.st_mtime_ns}:{stat.st_size}:{title}:{bool(key)}"
                cached = db.execute("SELECT tags FROM song_moods WHERE path=? AND fingerprint=?",
                                    (name, fingerprint)).fetchone()
                tags = json.loads(cached[0]) if cached else song_tags(path, title + " " + metadata.get("genre", ""), key)
                if not cached:
                    with db:
                        db.execute("INSERT OR REPLACE INTO song_moods VALUES(?,?,?)",
                                   (name, fingerprint, json.dumps(tags)))
            except (OSError, subprocess.SubprocessError, ValueError):
                continue
            matches.append((music_distance(speech, tags), asset_id, path, title, metadata, tags))
        if not matches:
            raise ValueError("No usable song could be matched to the speech.")
        distance, asset_id, path, title, metadata, tags = min(matches, key=lambda row: row[0])
        decision = speech | {"song": title, "song_tags": tags, "distance": round(distance, 3),
                             "reason": "Closest available song by editorial mood and measured energy."}
        with db:
            db.execute("UPDATE reels SET song_id=?, music_decision=? WHERE id=?",
                       (asset_id, json.dumps(decision), reel_id))
        offset = segment_starts([metadata])[0] if metadata else 0
        return path, offset

    def _render(self, reel_id):
        with closing(sqlite3.connect(self.database)) as db:
            with db:
                row = db.execute(
                    """UPDATE reels SET status='rendering',render_claim=unixepoch()
                       WHERE id=? AND (status='queued' OR
                                       (status='rendering' AND render_claim < unixepoch()-3600))
                       RETURNING source,format_id,song_id,video_text""", (reel_id,)).fetchone()
            if row is None:
                return
            source, format_id, song_id, stored_text = row
            error = None
            skipped = False
            production = False
            self.rendering_id = reel_id
            try:
                if format_id:
                    if self.reel_builder is None:
                        raise RuntimeError("Configured reel renderer is unavailable")
                    song = db.execute("SELECT path FROM library WHERE id=?", (song_id,)).fetchone()
                    clip = db.execute("SELECT id FROM library WHERE path=?", (source,)).fetchone()
                    if song is None or clip is None:
                        raise ValueError("A reel media source is no longer available")
                    args = (Path(source), Path(song[0]), self.directory / f"{reel_id}.mp4", format_id)
                    imported = {path: json.loads(metadata) for path, metadata in db.execute(
                        "SELECT path,metadata FROM imported_media WHERE (path=? AND kind='clip') OR (path=? AND kind='song')",
                        (source, song[0]))}
                    production = len(imported) == 2
                    styling = {}
                    if getattr(self.reel_builder, "generate_captions", False) is True and format_id != "F1":
                        context = imported.get(source, {}).get("title") or "gym training"
                        video_text = json.loads(stored_text) if stored_text else None
                        if format_id in {"L", "P", "F4"}:
                            video_text = None
                        if video_text is None:
                            video_text = self._reserve_video_text(db, reel_id, format_id, context)
                        styling = {"video_text": video_text, "caption_preset": self.settings()["caption_preset"]}
                    if format_id == "F3" and styling:
                        styling["voice_id"] = self.settings()["kokoro_voice"]
                    if format_id == "F1":
                        voice = db.execute("""SELECT library.path,metadata FROM library LEFT JOIN imported_media
                            USING(path) WHERE library.kind='speech' AND retired=0 AND (?=0 OR metadata IS NOT NULL)
                            ORDER BY metadata IS NOT NULL DESC,pinned DESC,id LIMIT 1""",
                                           (int(production),)).fetchone()
                        if not voice:
                            raise NoSpeech("F1 skipped: add a separate speech source.")
                        starts = segment_starts([imported[source], imported[song[0]]]) if production else (0, 0, 0)
                        def select_music(mood):
                            nonlocal clip
                            clip_id, video, start, reason = self._match_footage(db, mood, production, source)
                            clip = (clip_id,)
                            path, offset = self._match_song(db, reel_id, mood | {"footage_reason": reason}, production)
                            with db:
                                db.execute("UPDATE reels SET source=? WHERE id=?", (str(video), reel_id))
                            return path, offset, video, start

                        self.reel_builder(*args, speech_source=Path(voice[0]), starts=starts,
                                          select_music=select_music, **styling)
                        song_id = db.execute("SELECT song_id FROM reels WHERE id=?", (reel_id,)).fetchone()[0]
                    elif format_id in {"P", "L", "F4"} and styling:
                        mood = {"mood": video_text["mood"], "energy": video_text["energy"],
                                "passage_reason": video_text["theme"]}
                        clip_id, video, start, reason = self._match_footage(db, mood, production, source)
                        clip = (clip_id,)
                        path, offset = self._match_song(db, reel_id, mood | {"footage_reason": reason}, production)
                        with db:
                            db.execute("UPDATE reels SET source=? WHERE id=?", (str(video), reel_id))
                        self.reel_builder(video, path, args[2], format_id, starts=(start, offset, 0), **styling)
                        song_id = db.execute("SELECT song_id FROM reels WHERE id=?", (reel_id,)).fetchone()[0]
                    elif production:
                        self.reel_builder(*args, starts=segment_starts([imported[source], imported[song[0]]]), **styling)
                    else:
                        self.reel_builder(*args, **styling)
                    self.performance.register(reel_id, song_id, clip[0], format_id)
                else:
                    self._render_preview(reel_id, source)
            except NoSpeech as exc:
                skipped, error = True, str(exc)
            except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as exc:
                error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            finally:
                self.rendering_id = None
            with db:
                db.execute("UPDATE reels SET status=?, error=?, production=? WHERE id=?",
                           ("skipped" if skipped else "failed" if error else "review", error, int(production and not error), reel_id))

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
                                 "status IN ('review','rejected','failed','queued','skipped')", (reel_id,)).rowcount
            if not changed:
                raise ValueError("Reel cannot be deleted")

    def reel_details(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("""
                SELECT reels.id, status, error, source, format_id, library.path, production
                FROM reels LEFT JOIN library ON library.id = reels.song_id
                WHERE status != 'deleted' ORDER BY reels.id DESC
            """).fetchall()

    def list_reels(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT id,status,error FROM reels WHERE status != 'deleted' ORDER BY id DESC").fetchall()

    def _loop(self):
        while not self.stop_signal.is_set():
            with self.render_lock:
                with closing(sqlite3.connect(self.database)) as db:
                    row = db.execute("SELECT id FROM reels WHERE status='queued' OR (status='rendering' AND render_claim < unixepoch()-3600) ORDER BY id LIMIT 1").fetchone()
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

    def shutdown(self):
        self.stop()
        if self.worker is not None:
            self.worker.join()

    def stop(self):
        with self.lifecycle:
            self.stop_signal.set()


def segment_starts(segments):
    starts = tuple(max(0, min(row["timestamp"], row["duration"] - 25)) for row in segments)
    return starts + (0,) * (3 - len(starts))
