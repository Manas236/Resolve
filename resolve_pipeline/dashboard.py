"""Local read-only report dashboard."""

import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            try:
                payload = report_html(json.loads(args.report.read_text(encoding="utf-8"))).encode()
            except (OSError, ValueError):
                self.send_error(503, "Probe report unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
