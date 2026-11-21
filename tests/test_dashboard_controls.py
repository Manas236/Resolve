from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
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
            pipeline = Pipeline(root / "state", [source])
            with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(pipeline, root / "report")) as server:
                http = Thread(target=server.serve_forever)
                http.start()
                connection = HTTPConnection(*server.server_address, timeout=30)
                try:
                    connection.request("GET", "/")
                    page = connection.getresponse().read().decode()
                    token = re.search(r'name="token" value="([^"]+)"', page)[1]

                    def post(action, **fields):
                        connection.request("POST", "/" + action, urlencode({"token": token, **fields}))
                        response = connection.getresponse()
                        response.read()
                        return response.status

                    self.assertEqual(post("make", count=2, token="wrong"), 403)
                    self.assertEqual(post("make", count=2), 303)
                    self.assertFalse(pipeline.running)
                    for reel in (1, 2):
                        self.assertGreater((pipeline.directory / f"{reel}.mp4").stat().st_size, 0)
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
                    self.assertEqual(post("stop"), 303)
                    pipeline.worker.join(5)
                    self.assertFalse(pipeline.worker.is_alive())
                finally:
                    connection.close()
                    pipeline.stop()
                    server.shutdown()
                    http.join()
