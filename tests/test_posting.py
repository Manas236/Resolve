from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from tempfile import TemporaryDirectory
from pathlib import Path
from threading import Thread
import unittest
from urllib.parse import parse_qs, urlsplit

from resolve_pipeline.posting import MetaPublisher, Post, PostingError, RateLimited


class MetaPublisherTests(unittest.TestCase):
    def test_dry_run_and_live_gate(self):
        dry = MetaPublisher("instagram", version="v24.0")
        self.assertEqual(dry.publish(Post("3", "", "")).remote_id, "dry-run:3")
        live = MetaPublisher("instagram", version="v24.0", account_id="1", token="test",
                             dry_run=False, graph_url="http://127.0.0.1:1")
        with self.assertRaisesRegex(PostingError, "authorization"):
            live.publish(Post("3", "https://example.org/reel.mp4", ""))
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            live.publish(Post("3", "http://example.org/reel.mp4", "", authorized=True))

    def test_live_request_sequences_against_fake_graph(self):
        def serve(scenarios):
            with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
                server.scenarios = list(scenarios)
                server.requests = []
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    yield server, f"http://127.0.0.1:{server.server_port}"
                finally:
                    server.shutdown()
                    thread.join()

        class Handler(BaseHTTPRequestHandler):
            def request(self):
                address = urlsplit(self.path)
                data = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                self.server.requests.append((self.command, address.path, parse_qs(data or address.query),
                                             self.headers.get("Authorization"), self.headers.get("file_url")))
                if self.headers.get("file_size"):
                    self.server.local_upload = (data, self.headers["file_size"], self.headers["offset"])
                status, payload = self.server.scenarios.pop(0)
                payload = payload(self.server.server_port) if callable(payload) else payload
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = request

            def log_message(self, *args):
                pass

        post = Post("1", "https://example.org/reel.mp4", "Caption #goal", authorized=True)

        def publisher(platform, base):
            return MetaPublisher(platform, version="v24.0", account_id="42", token="fake",
                                 dry_run=False, graph_url=base, upload_url=base, poll_interval=0,
                                 poll_attempts=2)

        with self.subTest(platform="instagram"):
            for server, base in serve([
                (200, {"id": "10"}), (503, {}), (200, {"status_code": "IN_PROGRESS"}),
                (200, {"status_code": "FINISHED"}), (200, {"id": "20"}),
            ]):
                self.assertEqual(publisher("instagram", base).publish(post).remote_id, "20")
                self.assertEqual([req[1] for req in server.requests],
                                 ["/v24.0/42/media", "/v24.0/10", "/v24.0/10",
                                  "/v24.0/10", "/v24.0/42/media_publish"])
                self.assertEqual(server.requests[0][2]["caption"], ["Caption #goal"])
                self.assertEqual(server.requests[-1][2], {"creation_id": ["10"]})
                self.assertEqual(server.requests[0][3], "Bearer fake")

        with self.subTest(platform="facebook"):
            for server, base in serve([
                (200, lambda port: {"video_id": "30", "upload_url": f"http://127.0.0.1:{port}/video-upload/x"}),
                (200, {"success": True}),
                (200, {"status": {"video_status": "processing", "uploading_phase": {"status": "complete"}}}),
                (200, {"success": True}),
            ]):
                self.assertEqual(publisher("facebook", base).publish(post).remote_id, "30")
                self.assertEqual([req[1] for req in server.requests],
                                 ["/v24.0/42/video_reels", "/video-upload/x", "/v24.0/30", "/v24.0/42/video_reels"])
                self.assertEqual(server.requests[1][3:], ("OAuth fake", post.video_url))
                self.assertEqual(server.requests[-1][2]["upload_phase"], ["finish"])

        with TemporaryDirectory() as folder:
            source = Path(folder) / "local reel.mp4"
            source.write_bytes(b"video bytes")
            local = Post("1", source, "Caption", authorized=True)
            for platform, key, prefix in (("instagram", "uri", "ig-api-upload"), ("facebook", "upload_url", "video-upload")):
                status = ({"status_code": "FINISHED"} if platform == "instagram"
                          else {"status": {"uploading_phase": {"status": "complete"}}})
                identifier = "id" if platform == "instagram" else "video_id"
                for server, base in serve([
                    (200, lambda port: {identifier: "30", key: f"http://127.0.0.1:{port}/{prefix}/30"}),
                    (200, {"success": True}), (200, status), (200, {"success": True, "id": "30"}),
                ]):
                    self.assertEqual(publisher(platform, base).publish(local).remote_id, "30")
                    self.assertEqual(server.local_upload, ("video bytes", "11", "0"))
                    if platform == "instagram":
                        self.assertEqual(server.requests[0][2]["upload_type"], ["resumable"])
                        self.assertNotIn("video_url", server.requests[0][2])

        for scenarios, exception, count in (
            ([(429, {})], RateLimited, 1),
            ([(400, {"error": {"code": 613}})], RateLimited, 1),
            ([(200, {"id": "10"}), (200, {"status_code": "ERROR"})], PostingError, 2),
            ([(200, {"id": "10"}), (200, {"status_code": "IN_PROGRESS"}),
              (200, {"status_code": "IN_PROGRESS"})], PostingError, 3),
        ):
            for server, base in serve(scenarios):
                with self.assertRaises(exception):
                    publisher("instagram", base).publish(post)
                self.assertEqual(len(server.requests), count)

        for server, base in serve([(200, {"video_id": "30",
                                           "upload_url": "https://example.org/video-upload/x"})]):
            with self.assertRaisesRegex(PostingError, "configured upload origin"):
                publisher("facebook", base).publish(post)
            self.assertEqual(len(server.requests), 1)
