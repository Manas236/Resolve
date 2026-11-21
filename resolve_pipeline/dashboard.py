"""Local Resolve dashboard with pipeline controls."""

import argparse
from html import escape
from http.cookies import SimpleCookie, CookieError
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from hmac import compare_digest
from secrets import token_urlsafe
from urllib.parse import parse_qs

from .control import Pipeline
from .dashboard_auth import DashboardAuth
from .config import read_env_file
from pathlib import Path


def report_html(report):
    rows = []
    for provider, kinds in report.get("provider_summary", {}).items():
        for kind, stats in kinds.items():
            cells = (provider, kind, stats.get("verified", 0), stats.get("target", 0))
            rows.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in cells) + "</tr>")
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Resolve</title>'
        '<h1>Resolve probe results</h1><table><thead><tr><th>Provider</th>'
        '<th>Media</th><th>Verified</th><th>Targets</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table></html>"
    )


def handler_for(pipeline, report, auth=None):
    token = token_urlsafe(32)

    def form(action, fields, label):
        return (f'<form method="post" action="/{action}">'
                f'<input type="hidden" name="token" value="{token}">{fields}'
                f'<button>{label}</button></form>')

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

        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            if auth is not None and not auth.valid(self.session()):
                payload = form("login", '<label>Password <input type="password" name="password" required></label>', "Log in").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            status = "Running" if pipeline.running else "Stopped"
            controls = (f"<h2>Pipeline</h2><p>{status}</p>"
                        + form("make", '<label>Count <input name="count" type="number" min="1" max="20" value="1" required></label>', "Make N reels now")
                        + form("start", "", "Start runner") + form("stop", "", "Stop runner"))
            if auth is not None:
                controls += form("logout", "", "Log out")
            for reel_id, state, error in pipeline.list_reels():
                controls += f"<p>Reel {reel_id} · {state} {escape(error or '')}</p>"
                if state in ("review", "rejected", "failed", "queued"):
                    field = f'<input type="hidden" name="id" value="{reel_id}">'
                    controls += form("delete", field, "Delete")
                    if state != "queued":
                        controls += form("regenerate", field, "Regenerate")
                    if state == "review":
                        controls += form("approve", field, "Approve") + form("reject", field, "Reject")
            try:
                data = json.loads(report.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            payload = report_html(data).replace("</html>", controls + "</html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2048:
                    raise ValueError("Invalid request length")
                fields = parse_qs(self.rfile.read(size).decode(), strict_parsing=True)
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
                    pipeline.make_now(int(fields["count"][0]))
                elif self.path in ("/approve", "/reject"):
                    pipeline.review(int(fields["id"][0]), "approved" if self.path == "/approve" else "rejected")
                elif self.path == "/regenerate":
                    pipeline.regenerate(int(fields["id"][0]))
                elif self.path == "/delete":
                    pipeline.delete(int(fields["id"][0]))
                elif self.path == "/start":
                    pipeline.start()
                elif self.path == "/stop":
                    pipeline.stop()
                else:
                    self.send_error(404)
                    return
            except (ValueError, KeyError) as error:
                self.send_error(400, explain=str(error))
                return
            self.redirect()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state", type=Path, default=Path("state/dashboard"))
    parser.add_argument("--source", type=Path, action="append", required=True)
    args = parser.parse_args()
    settings = read_env_file(Path(".env")) | dict(os.environ)
    try:
        auth = DashboardAuth(settings.get("RESOLVE_DASHBOARD_PASSWORD_HASH", ""))
    except ValueError as error:
        parser.error(str(error))
    pipeline = Pipeline(args.state, args.source)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(pipeline, args.report, auth)) as server:
        try:
            server.serve_forever()
        finally:
            pipeline.stop()


if __name__ == "__main__":
    main()
