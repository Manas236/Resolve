import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
from contextlib import closing

from resolve_pipeline.acquisition_keys import asset_keys
from resolve_pipeline.bundles import inventory_keys, receive_bundles
from resolve_pipeline.control import Pipeline
from tools import pc_discovery, pc_vision


class DiscoveryTests(unittest.TestCase):
    def test_sources_rank_and_dedupe_against_library(self):
        playlists = ["https://example.com/a", "https://example.com/b"]
        article = "https://example.com/phonk"
        training = "https://example.com/training"
        release = {"id": "abcdefghijk", "duration": 100, "title": "Heavy Phonk Artist",
                   "channel": "Artist", "view_count": 20}
        workout = {"id": "ABCDEFGHIJK", "duration": 45, "title": "Squat training"}
        song_url = "https://www.youtube.com/watch?v=abcdefghijk"
        clip_url = "https://www.youtube.com/watch?v=ABCDEFGHIJK"
        track_id = "abcdefghijklmnopqrstuv"
        document = {"props": {"pageProps": {"state": {"data": {"entity": {
            "type": "playlist", "trackList": [{"entityType": "track", "title": "Heavy Phonk",
                                               "subtitle": "Artist", "duration": 100000,
                                               "uri": "spotify:track:" + track_id}]}}}}}}
        pages = {url: f'<script id="__NEXT_DATA__">{json.dumps(document)}</script>' for url in playlists}
        pages[article] = f'<a href="{song_url}">Track</a>'
        pages[training] = f'<iframe src="{clip_url}"></iframe>'
        config = {"spotify_playlists": playlists, "song_articles": [article],
                  "training_pages": [training], "training_searches": ["squats"]}
        search = lambda target: [workout] if target in {clip_url, "ytsearch15:squats"} else [release]
        rows = pc_discovery.discover(config, [], set(), pages.__getitem__, search)
        self.assertEqual([row["source"] for row in rows], [song_url, clip_url])
        self.assertEqual(rows[0]["duration_reference"], "spotify")
        self.assertEqual(set(rows[0]["source_pages"]), {*playlists, article})
        imported = {"source": "https://youtu.be/abcdefghijk", "kind": "song"}
        rows = pc_discovery.discover(config, [], asset_keys(imported), pages.__getitem__, search)
        self.assertEqual([row["kind"] for row in rows], ["clip"])

    def test_pin_priority_and_recording_dedupe(self):
        entry = {"kind": "song", "title": "Heavy! — The Artist", "spotify_id": "123",
                 "source": "https://youtu.be/abcdefghijk", "pinned": True}
        other = {**entry, "title": "HEAVY — The Artist", "source": "https://youtu.be/ABCDEFGHIJK",
                 "pinned": False, "source_pages": ["one", "two"]}
        self.assertEqual(list(pc_discovery.rank([other, entry], set())), [entry])

    def test_server_exports_existing_imported_media(self):
        from datetime import datetime, timezone

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = Pipeline(root / "state", [])
            metadata = {"kind": "clip", "source": "https://youtu.be/abcdefghijk"}
            with closing(sqlite3.connect(pipeline.database)) as db, db:
                db.execute("INSERT INTO imported_media VALUES(?,?,?)", ("file", "clip", json.dumps(metadata)))
                self.assertEqual(inventory_keys(db), asset_keys(metadata))
            receive_bundles(pipeline, root, datetime.now(timezone.utc), None)
            self.assertEqual(set(json.loads((root / "inventory.json").read_text())), asset_keys(metadata))


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
class VisionTests(unittest.TestCase):
    def test_extracts_fixture_frames_and_filters_by_majority_and_threshold(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = root / "fixture.mp4"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=red:s=96x64:r=5:d=26", "-c:v", "libx264", str(clip)], check=True)
            model = pc_vision.TrainingVision.__new__(pc_vision.TrainingVision)
            model.model, model.threshold = "fixture", 0.65

            def fixture_predict(frame, candidate_labels, hypothesis_template):
                path = Path(frame)
                self.assertTrue(path.read_bytes().startswith(b"\xff\xd8"))
                label = candidate_labels[0 if int(path.stem[-1]) < 3 else 4]
                return [{"label": name, "score": 0.8 if name == label else 0.02}
                        for name in candidate_labels]

            model.classify = fixture_predict
            result = model.screen(clip, 26, 10, root)
            self.assertTrue(result["accepted"])
            self.assertEqual(len(result["frames"]), 5)
            self.assertEqual(sum(frame["passed"] for frame in result["frames"]), 3)
            frames = [(i, path) for i, path in enumerate(sorted(root.glob("screen-*.jpg")))]
            self.assertFalse(pc_vision.score_frames(frames, fixture_predict, 0.95)["accepted"])
            self.assertFalse(pc_vision.score_frames(frames[:4], fixture_predict, 0.65)["accepted"])


class RefreshTests(unittest.TestCase):
    def test_no_catalog_backfills_rejected_clip_and_keeps_scores(self):
        from unittest.mock import patch
        from zipfile import ZipFile
        from tools import pc_acquisition

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources.json"
            sources.write_text("{}", encoding="utf-8")
            output = root / "batch.zip"
            entries = [{"kind": "clip", "source": f"https://youtu.be/abcdefghij{i}"}
                       for i in range(3)]

            def acquire(item, folder):
                path = folder / "clip.mp4"
                path.write_bytes(b"fixture")
                return path, {**item, "duration": 30, "timestamp": 2}, {}

            with patch.object(pc_acquisition.pc_discovery, "discover", return_value=entries) as discover, \
                    patch.object(pc_acquisition, "checked_asset", side_effect=acquire) as download, \
                    patch.object(pc_acquisition, "fetch_inventory", return_value={"existing"}), \
                    patch.object(pc_acquisition, "sync_bundle") as sync, \
                    patch.object(pc_acquisition.pc_vision, "TrainingVision") as model:
                model.return_value.screen.side_effect = [{"accepted": False}, {"accepted": True}]
                pc_acquisition.refresh(None, output, root / ".env", True,
                                       {"clip": 1}, 0.65, sources)
            self.assertEqual(discover.call_args.args[2], {"existing"})
            self.assertEqual(download.call_count, 2)
            sync.assert_called_once()
            with ZipFile(output) as archive:
                entry, = json.loads(archive.read("manifest.json"))["entries"]
            self.assertEqual(entry["source"], entries[1]["source"])
            self.assertEqual(entry["content"], "physical_training")
            self.assertTrue(entry["vision"]["accepted"])
            report = json.loads(output.with_suffix(".discovery.json").read_text())
            self.assertFalse(report["candidates"][0]["vision"]["accepted"])
