from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from resolve_pipeline.bundles import import_bundle, write_bundle
from resolve_pipeline.control import Pipeline
from resolve_pipeline.posting import MetaPublisher
from resolve_pipeline.reels import LocalReelBuilder
from resolve_pipeline.refresh import RefreshJobs
from resolve_pipeline.scheduler import Scheduler


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Requires FFmpeg")
class DryRunFlowTest(unittest.TestCase):
    def test_refresh_to_render_to_approved_dry_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, song = root / "clip.mp4", root / "song.wav"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                 "color=gray:s=1280x720:r=2:d=22", "-c:v", "libx264", str(source)], check=True,
            )
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                 "sine=frequency=350:duration=22", str(song)], check=True,
            )
            builder = LocalReelBuilder(hook="", generate_captions=True)
            pipeline = Pipeline(root / "state", [], reel_builder=builder)
            bundle = root / "sample.zip"
            write_bundle(bundle, [
                (source, {"kind": "clip", "content": "physical_training", "source": "fixture:clip",
                          "timestamp": 0, "duration": 22}),
                (song, {"kind": "song", "genre": "phonk", "source": "fixture:song",
                        "timestamp": 0, "duration": 22}),
            ])
            refresh = RefreshJobs(
                pipeline.database, songs=lambda: import_bundle(pipeline, bundle), clips=lambda: pipeline.library(),
                site_health=lambda: pipeline.request_site_retest(root / "jobs/next_job.json"),
            )
            runner = Scheduler(pipeline, {
                name: MetaPublisher(name, version="v24.0")
                for name in ("instagram", "facebook")
            }, lambda reel: "", refresh=refresh)
            now = datetime(2026, 11, 22, 9, tzinfo=timezone.utc)
            self.assertEqual(runner.tick(now), [])
            self.assertTrue((root / "jobs/next_job.json").is_file())
            by_kind = {row[2]: Path(row[1]) for row in pipeline.library()}
            self.assertEqual(set(by_kind), {"song", "clip"})
            self.assertFalse(import_bundle(pipeline, bundle))
            fixture = dict(id="test-only", creator="fixture", speaker="fixture", formats=["L"],
                           text="An existing fixture sentence.", mood="reflective", energy=.4, theme="fixture")
            bank = patch("resolve_pipeline.quotes.genre_catalog", return_value={fixture["id"]: fixture})
            bank.start()
            self.addCleanup(bank.stop)
            pipeline.set_quote_state(fixture["id"], "active")
            reel_id, = pipeline.enqueue(1, "L", production_only=True, production_day="2026-11-22")
            self.assertEqual(pipeline.enqueue(1, "L", production_only=True, production_day="2026-11-22"), [])
            pipeline._render(reel_id)
            path = pipeline.directory / f"{reel_id}.mp4"
            self.assertGreater(path.stat().st_size, 0)
            self.assertEqual(pipeline.list_reels(), [(reel_id, "review", None)])
            self.assertEqual(runner.tick(now), [])
            self.assertTrue(pipeline.telegram_review("dry-run-review", reel_id, "approve"))
            self.assertEqual([(platform, status) for _, platform, status in runner.tick(now)],
                             [("facebook", "dry_run"), ("instagram", "dry_run")])
            self.assertEqual(runner.tick(now), [])
