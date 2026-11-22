"""Opt-in acceptance test for a host with sandboxed Chromium installed."""

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from http.server import ThreadingHTTPServer
import unittest

from resolve_pipeline.control import Pipeline
from resolve_pipeline.dashboard import handler_for
from resolve_pipeline.dashboard_auth import DashboardAuth, password_hash
from tools.m0_probe import start_browser


@unittest.skipUnless(os.environ.get("RESOLVE_BROWSER_E2E") == "1", "Requires sandboxed Chromium")
@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Requires FFmpeg")
class BrowserControlsTests(unittest.TestCase):
    def test_controls_and_in_place_updates(self):
        with start_browser(os.environ.get("RESOLVE_BROWSER_EXECUTABLE")) as browser:
            for running in (False, True):
                with self.subTest(runner_initially_running=running):
                    self.exercise(browser, running)

    def exercise(self, browser, running):
        from playwright.sync_api import expect

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, song = root / "clip.mp4", root / "song.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi",
                            "-i", "color=blue:r=2:d=4", "-f", "lavfi", "-i", "sine=d=4",
                            "-c:v", "libx264", "-c:a", "aac", str(source)], check=True, timeout=15)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi",
                            "-i", "sine=d=4", str(song)], check=True, timeout=15)
            pipeline = Pipeline(root / "state", [source])
            pipeline.add_song(song)
            if running:
                pipeline.start()
            salt = b"local browser acceptance"
            auth = DashboardAuth(salt.hex() + "$" + password_hash("resolve-test", salt))
            job = root / "jobs/next_job.json"
            report = root / "report.json"
            report.write_text('{"provider_summary":{"mp7":{"audio":{"verified":0,"target":10}}}}')
            with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
                pipeline, report, auth=auth, job_file=job,
                performance={"reel": {"1": 50}, "song": {"a": 50}, "clip": {"b": 50}, "format": {"F1": 50}},
            )) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                page = browser.new_page()
                try:
                    self.drive(page, expect, pipeline, job, f"http://127.0.0.1:{server.server_port}", running)
                finally:
                    page.close()
                    pipeline.shutdown()
                    server.shutdown()
                    thread.join()

    def drive(self, page, expect, pipeline, job, origin, initially_running):
        page.goto(origin)
        page.locator('input[name="password"]').fill("resolve-test")
        with page.expect_navigation():
            page.get_by_role("button", name="Log in").click()
        self.assertEqual(pipeline.running, initially_running)
        def navigate(route):
            page.get_by_role("navigation").get_by_role("link", name=route, exact=True).click()
            page.evaluate("window.reviewPageMarker = 17")

        page.evaluate("window.reviewPageMarker = 17")
        navigate("Libraries")

        def submit(route, *, field=None, value=None, success=True):
            form = page.locator(f'form[action="/{route}"]')
            if field is not None:
                form = form.filter(has=page.locator(f'input[name="{field}"][value="{value}"]'))
            with page.expect_response(lambda response: response.request.method == "POST"
                                      and response.url == f"{origin}/{route}") as received:
                form.get_by_role("button").click()
            self.assertEqual(received.value.status, 303 if success else 400)
            feedback = page.locator("#feedback")
            if success:
                expect(feedback).to_have_text("Saved", timeout=10000)
                page.evaluate("document.getElementById('feedback').textContent = ''")
            else:
                expect(feedback).not_to_have_text("")
                page.evaluate("document.getElementById('feedback').textContent = ''")
            self.assertEqual(page.evaluate("window.reviewPageMarker"), 17)

        for identifier in (1, 2):
            preview = page.request.get(f"{origin}/preview/{identifier}")
            self.assertEqual(preview.status, 200)
            self.assertGreater(len(preview.body()), 100)
            submit("library-pin", field="id", value=identifier)
            submit("library-timestamp", field="id", value=identifier)
        navigate("Make")
        page.locator('input[name="count"]').fill("3")
        submit("make")
        self.assertEqual(len(pipeline.list_reels()), 3)
        page.locator('input[name="url"]').fill("https://example.org/test.mp4")
        submit("make-url", success=False)
        navigate("Logs")
        expect(page.locator("h2", has_text="Logs and failures")).to_be_visible()
        navigate("Performance")
        expect(page.locator("h2", has_text="Performance (fixture)")).to_be_visible()
        for label in ("Reel", "Song", "Clip", "Format"):
            expect(page.get_by_role("heading", name=label, exact=True)).to_be_visible()
        navigate("Review")
        page.locator('[data-reel="1"] summary').click()
        edit = page.locator('form[action="/edit"]').filter(has=page.locator('input[name="id"][value="1"]'))
        edit.locator('textarea[name="caption"]').fill("Caption")
        edit.locator('input[name="hashtags"]').fill("#reel")
        submit("edit", field="id", value=1)
        for identifier in (1, 2):
            submit("approve", field="id", value=identifier)
        submit("reject", field="id", value=3)
        submit("regenerate", field="id", value=3)
        submit("delete", field="id", value=4)
        navigate("Queue")
        if page.locator(".queue-row").first.get_attribute("data-id") != "2":
            page.locator('[data-id="2"] [data-move="-1"]').click()
        submit("reorder")
        self.assertEqual(pipeline.posting_queue(), [2, 1])
        navigate("Settings")
        page.locator('[name="refresh_cadence"]').select_option("monthly")
        submit("settings")
        self.assertEqual(pipeline.settings()["refresh_cadence"], "monthly")
        navigate("Sources")
        submit("site-toggle", field="site", value="mp7")
        self.assertFalse(pipeline.sites()["mp7"])
        submit("site-retest")
        self.assertTrue(job.is_file())
        if not initially_running:
            from resolve_pipeline.dashboard_view import PAGES
            output = Path(__file__).resolve().parents[1] / "research" / "ui_screenshots"
            output.mkdir(parents=True, exist_ok=True)
            for theme in ("dark", "light"):
                for route in PAGES:
                    navigate(route)
                    if page.locator("html").get_attribute("data-theme") != theme:
                        page.locator("#theme").click()
                    expect(page.locator("html")).to_have_attribute("data-theme", theme)
                    if route == "Libraries":
                        expect(page.locator(".wave").first).to_have_attribute("data-loaded", "true")
                    page.screenshot(path=output / f"{route.lower()}-{theme}.png", full_page=True)
        navigate("Libraries")
        for identifier in (1, 2):
            submit("library-retire", field="id", value=identifier)
        if not pipeline.running:
            submit("start")
            expect(page.locator("#status-bar")).to_contain_text("Runner running")
        submit("stop")
        with page.expect_navigation():
            page.get_by_role("button", name="Log out").click()
        expect(page.get_by_role("button", name="Log in")).to_be_visible()
