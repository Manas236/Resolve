"""Local Resolve dashboard with pipeline controls."""

import argparse
from html import escape
from http.cookies import SimpleCookie, CookieError
import os
import re
import subprocess
from array import array
from sys import byteorder
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from hmac import compare_digest
from secrets import token_urlsafe
from urllib.parse import parse_qs

from .control import Pipeline
from . import dashboard_view as view
from .reels import configured_builder
from .caption_style import PRESETS, PREVIEW_DIR
from .dashboard_auth import DashboardAuth
from .config import read_env_file
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def performance_html(data, *, fixture=True):
    cards = []
    for dimension in ("daily", "reel", "song", "clip", "format"):
        entries = data.get(dimension, {})
        label = "Views over time (UTC)" if dimension == "daily" else dimension.title()
        scale = max((row.get("views", 0) if isinstance(row, dict) else row
                     for row in entries.values() if isinstance(row, (int, float, dict))), default=0)
        rows = []
        for key, metrics in entries.items():
            views = metrics.get("views", 0) if isinstance(metrics, dict) else metrics
            width = 100 * views / max(scale, 1) if type(views) in (int, float) else 0
            details = (f' · {metrics["observed"]} snapshots · {metrics["muted"]} mute signals'
                       if isinstance(metrics, dict) else "")
            rows.append(f'<div aria-label="{escape(str(key))} · {escape(str(views))} views{details}">'
                        f'<div class="chart-row"><span>{escape(str(key))}</span>'
                        f'<svg viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true">'
                        f'<rect width="{width:.2f}" height="12" rx="3"/></svg>'
                        f'<span>{escape(str(views))} views</span></div>'
                        f'<small>{details}</small></div>')
        message = "No dated snapshots yet." if dimension == "daily" else "No metrics available."
        cards.append(f'<section class="card"><h3>{label}</h3>' + ("".join(rows) or view.empty(message))
                     + '</section>')
    heading = "Performance (fixture)" if fixture else "Performance (24-hour snapshots)"
    return f"<h2>{heading}</h2><div class='grid'>" + "".join(cards) + "</div>"


def handler_for(pipeline, report, auth=None, job_file=None, performance=None, zone="UTC"):
    token = token_urlsafe(32)

    def form(action, fields, label):
        return (f'<form method="post" action="/{action}">'
                f'<input type="hidden" name="token" value="{token}">{fields}'
                f'<button class="{action}">{label}</button></form>')

    class Handler(BaseHTTPRequestHandler):
        def session(self):
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                return cookie["resolve_session"].value if "resolve_session" in cookie else ""
            except CookieError:
                return ""

        def redirect(self, cookie=None):
            self.send_response(303)
            self.send_header("Location", "/")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def payload(self, data, mime="text/html; charset=utf-8"):
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def media(self, path, mime):
            try:
                stream = path.open("rb")
            except OSError:
                self.send_error(404)
                return
            with stream:
                size = os.fstat(stream.fileno()).st_size
                start, end = 0, size - 1
                interval = self.headers.get("Range")
                if interval:
                    match = re.fullmatch(r"bytes=([0-9]{0,18})-([0-9]{0,18})", interval)
                    if match and any(match.groups()):
                        left, right = match.groups()
                        if left:
                            start = int(left)
                            end = min(end, int(right)) if right else end
                        else:
                            start = max(0, size - int(right))
                    if not match or not any(match.groups()) or start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                self.send_response(206 if interval else 200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Accept-Ranges", "bytes")
                if interval:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                remaining = end - start + 1
                self.send_header("Content-Length", str(remaining))
                self.end_headers()
                stream.seek(start)
                try:
                    while remaining and (chunk := stream.read(min(65536, remaining))):
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return

        def do_GET(self):
            static = {"/static/dashboard.css": ("dashboard.css", "text/css"),
                      "/static/dashboard.js": ("dashboard.js", "text/javascript")}
            if self.path in static:
                name, mime = static[self.path]
                self.payload(Path(__file__).with_name(name).read_bytes(), mime)
                return
            pages = {"/" + name.lower(): name for name in view.PAGES}
            pages["/"] = "Review"
            authenticated = auth is None or auth.valid(self.session())
            if not authenticated:
                if self.path not in pages:
                    self.send_error(403)
                    return
                login = form("login", '<label>Password <input type="password" name="password" required autofocus></label>', "Log in")
                self.payload(view.document(f'<main class="login card"><span class="brand"><span>R</span> Resolve</span>'
                                           f'<h1>Sign in</h1>{login}</main>').encode())
                return
            if self.path == "/dashboard-status":
                self.payload(view.status_bar(pipeline, form, zone).encode())
                return
            if self.path == "/render-status":
                active = pipeline.rendering_id
                rows = [{"id": key, "state": "rendering" if key == active and state == "queued" else state,
                         "error": error} for key, state, error in pipeline.list_reels()]
                self.payload(json.dumps(rows).encode(), "application/json")
                return
            if self.path.startswith("/caption-preset/"):
                key = self.path.rsplit("/", 1)[1]
                if key not in PRESETS:
                    self.send_error(404)
                    return
                self.media(PREVIEW_DIR / f"{key}.mp4", "video/mp4")
                return
            match = re.fullmatch(r"/(reel|preview|waveform)/([0-9]{1,18})", self.path)
            if match:
                kind, asset = match[1], int(match[2])
                if kind == "reel":
                    if asset not in {row[0] for row in pipeline.list_reels()}:
                        self.send_error(404)
                        return
                    self.media(pipeline.directory / f"{asset}.mp4", "video/mp4")
                    return
                entry = next((row for row in pipeline.library() if row[0] == asset), None)
                if entry is None or (kind == "waveform" and entry[2] != "song"):
                    self.send_error(404)
                    return
                path = Path(entry[1])
                if kind == "waveform":
                    try:
                        duration = float(subprocess.check_output(
                            ["ffprobe", "-v", "error", "-protocol_whitelist", "file",
                             "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                            timeout=10))
                        raw = subprocess.check_output(
                            ["ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-i", str(path),
                             "-t", "900", "-map", "0:a:0", "-ac", "1", "-ar", "2000", "-f", "s16le", "pipe:1"],
                            timeout=15)
                        samples = array("h", raw)
                        if byteorder != "little":
                            samples.byteswap()
                        step = max(1, (len(samples) + 95) // 96)
                        peaks = [max(map(abs, samples[i:i + step])) / 32768
                                 for i in range(0, len(samples), step)]
                    except (ValueError, OSError, subprocess.SubprocessError):
                        self.send_error(404, "Waveform unavailable")
                        return
                    self.payload(json.dumps({"peaks": peaks, "duration": min(duration, 900),
                                             "timestamp": entry[5]}).encode(), "application/json")
                    return
                mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
                        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
                        ".ogg": "audio/ogg", ".flac": "audio/flac"}.get(path.suffix.lower())
                if mime is None:
                    self.send_error(404)
                else:
                    self.media(path, mime)
                return
            page = pages.get(self.path)
            if page is None:
                self.send_error(404)
                return
            if page == "Review":
                content = view.review(pipeline, form)
            elif page == "Make":
                requirements = pipeline.format_requirements()
                availability = "".join(f"<li>{key} · {escape(reason or 'Available')}</li>"
                                       for key, reason in requirements.items())
                formats = "".join(f"<option{' disabled' if reason else ''}>{key}</option>"
                                  for key, reason in requirements.items())
                content = (
                    f"<ul>{availability}</ul>" +
                    '<div class="grid"><section class="card"><h2>Render a batch</h2>'
                    + form("make", '<label>Count <input name="count" type="number" min="1" max="20" value="1" required></label>'
                           '<label>Format <select name="format"><option value="auto">Auto</option>'
                           f'{formats}</select></label>', "Make N reels now")
                    + '</section><section class="card"><h2>Make from a URL</h2>'
                    + form("make-url", '<label>Media URL <input name="url" type="url" required></label>', "Make reel from URL")
                    + '</section></div><h2>Render progress</h2><div id="renders" role="status">No render in progress.</div>')
            elif page == "Queue":
                content = view.queue(pipeline, form, zone)
            elif page == "Libraries":
                content = view.libraries(pipeline, form)
            elif page == "Sources":
                try:
                    data = json.loads(report.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
                on_pc = job_file is not None and (job_file.parent / "pc-acquisition").exists()
                content = view.sources(pipeline, data, form, job_file is not None and not on_pc)
                if on_pc:
                    content = ('<p>Acquisition runs on Windows. Run the PC probe to retest sources. '
                               'The datacenter results below are historical.</p>' + content)
            elif page == "Settings":
                content = view.settings(pipeline, form, zone)
            elif page == "Performance":
                data = performance if performance is not None else {
                    dimension: pipeline.performance.grouped(dimension)
                    for dimension in ("reel", "song", "clip", "format")
                }
                if performance is None:
                    data["daily"] = pipeline.performance.daily_views()
                content = performance_html(data, fixture=performance is not None)
            else:
                content = view.logs(pipeline)
            logout = form("logout", "", "Log out") if auth is not None else ""
            self.payload(view.layout(page, view.status_bar(pipeline, form, zone), content, token, logout).encode())

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise ValueError("Invalid request length")
                fields = parse_qs(self.rfile.read(size).decode(), strict_parsing=True, keep_blank_values=True)
                if not compare_digest(fields.get("token", [""])[0].encode(), token.encode()):
                    self.send_error(403)
                    return
                if auth is not None:
                    if self.path == "/login":
                        session = auth.login(fields["password"][0])
                        if session is None:
                            self.send_error(403, "Login denied")
                        else:
                            self.redirect(f"resolve_session={session}; Path=/; HttpOnly; SameSite=Strict")
                        return
                    if not auth.valid(self.session()):
                        self.send_error(403)
                        return
                    if self.path == "/logout":
                        auth.logout(self.session())
                        self.redirect("resolve_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
                        return
                if self.path == "/make":
                    pipeline.make_now(int(fields["count"][0]), fields.get("format", ["auto"])[0])
                elif self.path in ("/approve", "/reject"):
                    pipeline.review(int(fields["id"][0]), "approved" if self.path == "/approve" else "rejected")
                elif self.path in ("/library-pin", "/library-retire", "/library-timestamp"):
                    pipeline.library_action(int(fields["id"][0]), self.path.removeprefix("/library-"))
                elif self.path == "/site-toggle":
                    pipeline.toggle_site(fields["site"][0])
                elif self.path == "/site-retest" and job_file is not None:
                    if (job_file.parent / "pc-acquisition").exists():
                        raise ValueError("Run the source probe from the Windows acquisition PC.")
                    pipeline.request_site_retest(job_file)
                elif self.path == "/settings":
                    pipeline.save_settings({key: values[0] for key, values in fields.items()})
                elif self.path == "/make-url":
                    pipeline.make_from_url(fields["url"][0])
                elif self.path == "/reorder":
                    order = fields["order"][0].strip()
                    pipeline.reorder([int(value) for value in order.split(",")] if order else [])
                elif self.path == "/edit":
                    pipeline.edit_reel(int(fields["id"][0]), fields["caption"][0], fields["hashtags"][0])
                elif self.path == "/edit-video-text":
                    pipeline.edit_video_text(int(fields["id"][0]), fields["hook"][0],
                                             fields.get("lines", [""])[0].strip().splitlines())
                elif self.path == "/render-caption-presets":
                    from .caption_preview import render_previews

                    render_previews()
                elif self.path == "/regenerate":
                    pipeline.regenerate(int(fields["id"][0]))
                elif self.path == "/delete":
                    pipeline.delete(int(fields["id"][0]))
                elif self.path == "/start":
                    pipeline.start()
                elif self.path == "/stop":
                    pipeline.shutdown()
                else:
                    self.send_error(404)
                    return
            except (ValueError, KeyError, ImportError, OSError, subprocess.SubprocessError) as error:
                if self.path != "/login" and (auth is None or auth.valid(self.session())):
                    pipeline.log_action(self.path.split("?", 1)[0][:64], "rejected")
                self.send_error(400, explain=str(error))
                return
            pipeline.log_action(self.path, "accepted")
            self.redirect()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--zone", default="UTC", help="Posting slot timezone, matching the operator")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state", type=Path, default=Path("state/dashboard"))
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--speech", type=Path, action="append", default=[])
    parser.add_argument("--song", type=Path, action="append", default=[])
    parser.add_argument("--job-file", type=Path, default=Path("jobs/next_job.json"))
    parser.add_argument("--performance-fixture", type=Path)
    parser.add_argument("--hook")
    parser.add_argument("--quote", default="", help="Override F2 lines separated by | or newlines")
    parser.add_argument("--voice-script", default="")
    parser.add_argument("--whisper-model", type=Path)
    parser.add_argument("--piper-model", type=Path)
    args = parser.parse_args()
    settings = read_env_file(Path(".env")) | dict(os.environ)
    try:
        ZoneInfo(args.zone)
        auth = DashboardAuth(settings.get("RESOLVE_DASHBOARD_PASSWORD_HASH", ""))
        builder = configured_builder(
            settings, hook=args.hook, quote=args.quote.replace("|", "\n"), voice_script=args.voice_script,
            whisper_model=args.whisper_model, piper_model=args.piper_model,
        )
    except (ValueError, ZoneInfoNotFoundError) as error:
        parser.error(str(error))
    pipeline = Pipeline(args.state, args.source, reel_builder=builder)
    for song in args.song:
        pipeline.add_song(song)
    for speech in args.speech:
        pipeline.add_speech(speech)
    performance = None
    if args.performance_fixture is not None:
        try:
            performance = json.loads(args.performance_fixture.read_text(encoding="utf-8"))
            if not isinstance(performance, dict) or any(not isinstance(value, dict)
                                                         for value in performance.values()):
                raise ValueError("Expected a JSON object of performance dimensions")
        except (OSError, ValueError) as error:
            parser.error(str(error))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(pipeline, args.report, auth, args.job_file, performance, args.zone)) as server:
        try:
            server.serve_forever()
        finally:
            pipeline.shutdown()


if __name__ == "__main__":
    main()
