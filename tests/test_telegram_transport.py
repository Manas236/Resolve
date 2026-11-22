from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from resolve_pipeline.control import Pipeline
from resolve_pipeline.telegram import ReviewBot, TelegramAPI


class TelegramTransportTests(unittest.TestCase):
    def test_preview_and_actions_are_idempotent_and_authorized(self):
        def update(index, action, reel, user=9, chat=17):
            return {"update_id": index, "callback_query": {
                "id": str(index), "from": {"id": user}, "message": {"chat": {"id": chat}},
                "data": f"{action}:{reel}",
            }}

        updates = [
            update(1, "approve", 1, user=999), update(2, "reject", 1, chat=999),
            update(3, "approve", 1), update(4, "reject", 1), update(5, "muted", 1),
            update(6, "muted", 1), update(7, "reject", 2), update(8, "regenerate", 2),
            update(9, "regenerate", 2),
        ]
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests.append((method, body))
                result = updates if method == "getUpdates" else True
                payload = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        with TemporaryDirectory() as directory, ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            root = Path(directory)
            pipeline = Pipeline(root, [root / "source.mp4"])
            with closing(sqlite3.connect(pipeline.database)) as db, db:
                db.executemany("INSERT INTO reels(source,status) VALUES (?, 'review')", [("source",)] * 2)
            (root / "1.mp4").write_bytes(b"preview bytes")
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                api = TelegramAPI("123:fake", base_url=f"http://127.0.0.1:{server.server_port}")
                bot = ReviewBot(api, pipeline, chat_id=17, reviewer_id=9)
                self.assertTrue(bot.send_preview(1))
                self.assertTrue(api.failure_alert(17, "A job failed"))
                self.assertEqual(requests[1][0], "sendMessage")
                self.assertEqual(json.loads(requests[1][1]), {"chat_id": 17, "text": "A job failed"})
                body = requests[0][1]
                self.assertIn(b"preview bytes", body)
                for action in ("approve", "reject", "regenerate", "muted"):
                    self.assertIn(f'"callback_data": "{action}:1"'.encode(), body)
                with patch.object(pipeline, "_render") as render:
                    self.assertEqual(bot.poll(), 10)
                    render.assert_called_once_with(3)
                restarted = Pipeline(root, [root / "source.mp4"])
                with patch.object(restarted, "_render") as render:
                    self.assertEqual(ReviewBot(api, restarted, chat_id=17, reviewer_id=9).poll(), 10)
                    render.assert_not_called()
                with closing(sqlite3.connect(pipeline.database)) as db:
                    self.assertEqual(db.execute("SELECT id,status FROM reels ORDER BY id").fetchall(),
                                     [(1, "approved"), (2, "rejected"), (3, "queued")])
                    self.assertEqual(db.execute(
                        "SELECT reel_id,action FROM telegram_reviews ORDER BY callback_id"
                    ).fetchall(), [(1, "approve"), (1, "muted"), (2, "reject"), (2, "regenerate")])
            finally:
                server.shutdown()
                thread.join()
