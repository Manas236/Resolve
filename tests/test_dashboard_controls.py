from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from urllib.parse import urlencode

from resolve_pipeline.control import Pipeline
from resolve_pipeline.dashboard import handler_for


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
class DashboardControlsTest(unittest.TestCase):
    def test_http_controls_render_review_and_manage_worker(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=s=160x90:r=2:d=1", "-c:v", "mpeg4", str(source)],
                           check=True, timeout=15)
            song = root / "song.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=4", str(song)],
                           check=True, timeout=15)
            pipeline = Pipeline(root / "state", [source])
            pipeline.add_song(song)
            job = root / "jobs" / "next_job.json"
            with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(pipeline, root / "report", job_file=job,
                                         performance={"song": {"<fixture>": 12}})) as server:
                http = Thread(target=server.serve_forever)
                http.start()
                connection = HTTPConnection(*server.server_address, timeout=30)
                try:
                    connection.request("GET", "/")
                    page = connection.getresponse().read().decode()
                    self.assertIn("&lt;fixture&gt; · 12 views", page)
                    token = re.search(r'name="token" value="([^"]+)"', page)[1]

                    def post(action, **fields):
                        connection.request("POST", "/" + action, urlencode({"token": token, **fields}))
                        response = connection.getresponse()
                        response.read()
                        return response.status

                    self.assertIn('replaceWith(updated)', page)
                    connection.request("GET", "/preview/2")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), song.read_bytes())
                    self.assertEqual(post("library-pin", id=2), 303)
                    self.assertEqual(post("library-timestamp", id=2), 303)
                    self.assertEqual(post("library-timestamp", id=1), 400)
                    self.assertEqual(pipeline.library()[1][3:], (1, 0, 0.0))
                    self.assertEqual(post("site-toggle", site="unknown"), 400)
                    self.assertEqual(post("site-toggle", site="mp7"), 303)
                    self.assertFalse(Pipeline(root / "state", [source]).sites()["mp7"])
                    self.assertEqual(post("site-retest"), 303)
                    initial_job = json.loads(job.read_text())
                    self.assertEqual(set(initial_job), {"id", "limit", "no_browser"})
                    settings = dict(posting_slots="18:00,09:00", volume=2, mode="auto",
                                    refresh_cadence="monthly", format_weights="3,2,1")
                    self.assertEqual(post("settings", **settings), 303)
                    self.assertEqual(Pipeline(root / "state", [source]).settings()["posting_slots"],
                                     "09:00,18:00")
                    self.assertEqual(post("settings", **(settings | {"volume": 3})), 400)
                    self.assertEqual(pipeline.settings()["volume"], "2")
                    self.assertEqual(post("make", count=2, token="wrong"), 403)
                    self.assertEqual(post("make", count=2), 303)
                    self.assertFalse(pipeline.running)
                    for reel in (1, 2):
                        self.assertGreater((pipeline.directory / f"{reel}.mp4").stat().st_size, 0)
                    self.assertEqual(post("make-url", url="https://example.com/a.mp4"), 400)
                    self.assertEqual(post("make-url", url="file:///tmp/video.mp4"), 400)
                    self.assertEqual(post("edit", id=1, caption="Train today", hashtags="#resolve"), 303)
                    self.assertEqual(pipeline.editorial()[1], ("Train today", "#resolve"))
                    self.assertEqual(post("approve", id=1), 303)
                    self.assertEqual(post("reject", id=2), 303)
                    self.assertEqual(post("approve", id=2), 400)
                    self.assertEqual(pipeline.list_reels(), [(2, "rejected", None), (1, "approved", None)])

                    pipeline.enqueue(1)
                    self.assertEqual(post("start"), 303)
                    deadline = time.monotonic() + 10
                    while pipeline.list_reels()[0][1] == "queued" and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertEqual(pipeline.list_reels()[0], (3, "review", None))
                    self.assertEqual(post("approve", id=3), 303)
                    self.assertEqual(post("reorder", order="3,3"), 400)
                    self.assertEqual(post("reorder", order="3,1"), 303)
                    self.assertEqual(pipeline.posting_queue(), [3, 1])
                    self.assertEqual(post("library-retire", id=2), 303)
                    self.assertEqual(post("library-pin", id=2), 400)
                    self.assertEqual(post("site-toggle", site="mp7"), 303)
                    self.assertTrue(pipeline.sites()["mp7"])
                    self.assertEqual(post("site-retest"), 303)
                    self.assertNotEqual(json.loads(job.read_text())["id"], initial_job["id"])
                    self.assertEqual(post("stop"), 303)
                    pipeline.worker.join(5)
                    self.assertFalse(pipeline.worker.is_alive())
                    self.assertEqual(post("regenerate", id=2), 303)
                    self.assertEqual(pipeline.list_reels()[0], (4, "review", None))
                    self.assertEqual(post("delete", id=4), 303)
                    self.assertEqual(post("approve", id=4), 400)
                    self.assertEqual(post("delete", id=1), 400)
                    self.assertEqual(pipeline.recent_actions()[0][1:], ("/delete", "rejected"))
                    connection.request("GET", "/")
                    self.assertIn("Logs and failures", connection.getresponse().read().decode())
                finally:
                    connection.close()
                    pipeline.stop()
                    server.shutdown()
                    http.join()
