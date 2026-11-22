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
from resolve_pipeline.dashboard_auth import DashboardAuth, password_hash


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
class DashboardControlsTest(unittest.TestCase):
    def test_http_controls_render_review_and_manage_worker(self):
        for initially_running in (False, True):
            with self.subTest(initially_running=initially_running):
                self.exercise_controls(initially_running)

    def exercise_controls(self, initially_running):
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
            salt = b"dashboard test salt"
            auth = DashboardAuth(salt.hex() + "$" + password_hash("test-password", salt))
            if initially_running:
                pipeline.start()
            with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(pipeline, root / "report", auth=auth, job_file=job,
                                         performance={"song": {"<fixture>": 12}})) as server:
                http = Thread(target=server.serve_forever)
                http.start()
                connection = HTTPConnection(*server.server_address, timeout=30)
                try:
                    connection.request("GET", "/")
                    page = connection.getresponse().read().decode()
                    token = re.search(r'name="token" value="([^"]+)"', page)[1]

                    cookie = ""

                    def post(action, **fields):
                        connection.request("POST", "/" + action, urlencode({"token": token, **fields}),
                                           {"Cookie": cookie})
                        response = connection.getresponse()
                        response.read()
                        return response.status

                    for route in ("/dashboard-status", "/render-status", "/reel/1", "/waveform/2"):
                        connection.request("GET", route)
                        result = connection.getresponse()
                        self.assertEqual(result.status, 403)
                        result.read()
                    self.assertIn("Log in", page)
                    self.assertEqual(post("make", count=1), 403)
                    connection.request("POST", "/login", urlencode({"token": token, "password": "test-password"}))
                    login = connection.getresponse()
                    self.assertEqual(login.status, 303)
                    cookie = login.getheader("Set-Cookie").split(";", 1)[0]
                    login.read()
                    connection.request("GET", "/performance", headers={"Cookie": cookie})
                    page = connection.getresponse().read().decode()
                    self.assertIn("&lt;fixture&gt; · 12 views", page)
                    self.assertIn("<svg", page)
                    for route in ("review", "make", "queue", "libraries", "sources", "settings", "performance", "logs"):
                        connection.request("GET", "/" + route, headers={"Cookie": cookie})
                        result = connection.getresponse()
                        self.assertEqual(result.status, 200)
                        self.assertIn(f'data-page="{route}"', result.read().decode())
                    connection.request("GET", "/static/dashboard.js")
                    self.assertIn("replaceWith(updated)", connection.getresponse().read().decode())
                    connection.request("GET", "/waveform/2", headers={"Cookie": cookie})
                    result = connection.getresponse()
                    self.assertTrue(json.loads(result.read())["peaks"])
                    connection.request("GET", "/preview/2", headers={"Cookie": cookie, "Range": "bytes=0-3"})
                    result = connection.getresponse()
                    self.assertEqual(result.status, 206)
                    self.assertEqual(result.read(), b"RIFF")
                    connection.request("GET", "/preview/2", headers={"Cookie": cookie})
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
                    self.assertEqual(pipeline.running, initially_running)
                    for reel in (1, 2):
                        self.assertGreater((pipeline.directory / f"{reel}.mp4").stat().st_size, 0)
                    connection.request("GET", "/reel/1", headers={"Cookie": cookie, "Range": "bytes=4-7"})
                    result = connection.getresponse()
                    self.assertEqual(result.status, 206)
                    self.assertEqual(result.read(), b"ftyp")
                    connection.request("GET", "/reel/1", headers={"Cookie": cookie, "Range": "bytes=999999999-"})
                    result = connection.getresponse()
                    self.assertEqual(result.status, 416)
                    result.read()
                    connection.request("GET", "/render-status", headers={"Cookie": cookie})
                    results = json.loads(connection.getresponse().read())
                    self.assertEqual({row["state"] for row in results}, {"review"})
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
                    while pipeline.list_reels()[0][1] in {"queued", "rendering"} and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertEqual(pipeline.list_reels()[0], (3, "review", None))
                    self.assertEqual(post("approve", id=3), 303)
                    self.assertEqual(post("reorder", order="3,3"), 400)
                    self.assertEqual(post("reorder", order="3,1"), 400)
                    self.assertEqual(pipeline.posting_queue(), [])
                    if not initially_running:
                        self.assertEqual(post("stop"), 303)
                        pipeline.worker.join(5)
                    self.assertEqual(post("library-retire", id=2), 303)
                    self.assertEqual(post("library-pin", id=2), 400)
                    self.assertEqual(post("site-toggle", site="mp7"), 303)
                    self.assertTrue(pipeline.sites()["mp7"])
                    self.assertEqual(post("site-retest"), 303)
                    self.assertNotEqual(json.loads(job.read_text())["id"], initial_job["id"])
                    self.assertEqual(post("regenerate", id=2), 303)
                    self.assertEqual(pipeline.list_reels()[0], (4, "review", None))
                    self.assertEqual(post("delete", id=4), 303)
                    self.assertEqual(post("approve", id=4), 400)
                    self.assertEqual(post("delete", id=1), 400)
                    self.assertEqual(pipeline.recent_actions()[0][1:], ("/delete", "rejected"))
                    connection.request("GET", "/logs", headers={"Cookie": cookie})
                    self.assertIn("Logs and failures", connection.getresponse().read().decode())
                    self.assertEqual(post("stop"), 303)
                    pipeline.worker.join(5)
                    self.assertFalse(pipeline.worker.is_alive())
                    self.assertEqual(post("logout"), 303)
                    self.assertEqual(post("make", count=1), 403)
                finally:
                    connection.close()
                    pipeline.shutdown()
                    server.shutdown()
                    http.join()
