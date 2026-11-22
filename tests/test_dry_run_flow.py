from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

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
                 "color=gray:s=240x180:r=2:d=22", "-c:v", "libx264", str(source)], check=True,
            )
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                 "sine=frequency=350:duration=22", str(song)], check=True,
            )
            builder = LocalReelBuilder(hook="Start now", quote="Start with one rep.")
            pipeline = Pipeline(root / "state", [source], reel_builder=builder)
            refresh = RefreshJobs(
                pipeline.database, songs=lambda: pipeline.add_song(song), clips=lambda: pipeline.library(),
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
            self.assertEqual(by_kind, {"song": song, "clip": source})
            reel_id, = pipeline.make_now(1, "F2")
            path = pipeline.directory / f"{reel_id}.mp4"
            self.assertGreater(path.stat().st_size, 0)
            self.assertEqual(pipeline.list_reels(), [(reel_id, "review", None)])
            self.assertEqual(runner.tick(now), [])
            self.assertTrue(pipeline.telegram_review("dry-run-review", reel_id, "approve"))
            self.assertEqual([(platform, status) for _, platform, status in runner.tick(now)],
                             [("facebook", "dry_run"), ("instagram", "dry_run")])
            self.assertEqual(runner.tick(now), [])
