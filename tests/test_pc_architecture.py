from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import random
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from resolve_pipeline.bundles import import_bundle, receive_bundles, write_bundle
from resolve_pipeline.control import Pipeline
from resolve_pipeline.reels import LocalReelBuilder, NoSpeech
from tools import pc_acquisition


class AcquisitionTests(unittest.TestCase):
    def test_bundle_checksum_rejected_and_stale_alert_once(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            pipeline = Pipeline(root / "state", [])
            inbox = root / "inbox"
            inbox.mkdir()
            original = root / "music.mp3"
            original.write_bytes(b"original")
            bundle = root / "original.zip"
            write_bundle(bundle, [(original, {"kind": "song", "source": "test", "timestamp": 0})])
            with ZipFile(bundle) as archive, ZipFile(inbox / "resolve-bad.zip", "w") as broken:
                broken.writestr("manifest.json", archive.read("manifest.json"))
                broken.writestr("media/0.mp3", b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                import_bundle(pipeline, inbox / "resolve-bad.zip")
            alerts = []
            now = datetime.now(timezone.utc)
            for days in (0, 8, 9, 10):
                receive_bundles(pipeline, inbox, now + timedelta(days=days), alerts.append)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(pipeline.library(), [])
            self.assertTrue((inbox / "resolve-bad.rejected").is_file())

    def test_completed_challenged_probe_keeps_full_denominators(self):
        with TemporaryDirectory() as folder:
            report = Path(folder) / "probe.json"
            with patch.object(pc_acquisition, "checked_asset", side_effect=ValueError("provider_challenge")) as acquire:
                pc_acquisition.probe(report)
            result = json.loads(report.read_text(encoding="utf-8"))
            acquire.assert_called_once()
            self.assertTrue(result["complete"])
            self.assertEqual(result["summary"], {kind: {"verified": 0, "target": 10}
                                                 for kind in ("audio", "video")})
            self.assertEqual(len(result["results"]), 20)
            self.assertEqual(result["results"][-1]["status"], "skipped_after_provider_challenge")

    def test_receive_retries_locked_bundle_and_processes_other_ready_files(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            pipeline = Pipeline(root / "state", [])
            inbox = root / "inbox"
            inbox.mkdir()
            locked, ready = (inbox / f"resolve-{name}.zip" for name in ("a", "b"))
            for path in (locked, ready):
                path.write_bytes(b"pending")
            now = datetime.now(timezone.utc)
            with patch("resolve_pipeline.bundles.import_bundle",
                       side_effect=[PermissionError("sharing violation"), True, True]) as importer:
                receive_bundles(pipeline, inbox, now, None)
                self.assertTrue(locked.is_file())
                self.assertFalse(locked.with_suffix(".rejected").exists())
                self.assertFalse(ready.exists())
                receive_bundles(pipeline, inbox, now, None)
                self.assertFalse(locked.exists())
                self.assertEqual([call.args[1] for call in importer.call_args_list], [locked, ready, locked])

    def test_preview_alignment_requires_unique_match(self):
        rng = random.Random(17)
        full = [rng.random() for _ in range(500)]
        reference = full[231:341]
        self.assertEqual(pc_acquisition.match_preview(full, reference), 23.1)
        self.assertIsNone(pc_acquisition.match_preview(full + full, reference))
        self.assertIsNone(pc_acquisition.match_preview(full, [0] * 110))

    def test_challenge_never_triggers_converter_fallback(self):
        with patch.object(pc_acquisition, "probe_replay", return_value={"status": "challenge_detected"}), \
                patch.object(pc_acquisition, "fallback") as fallback, \
                patch.object(pc_acquisition, "acquire") as acquire:
            with self.assertRaisesRegex(ValueError, "provider_challenge"):
                pc_acquisition.checked_asset(
                    {"source": "https://www.youtube.com/watch?v=example", "kind": "song"}, None)
            fallback.assert_not_called()
            acquire.assert_not_called()


class FormatReadinessTests(unittest.TestCase):
    def test_auto_uses_quote_without_whisper_and_skips_ineligible_phony_queue(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            builder = LocalReelBuilder(hook="Hook", quote="Quote", generate_captions=True)
            pipeline = Pipeline(root / "state", [root / "clip.mp4"], reel_builder=builder)
            (root / "song.wav").touch()
            pipeline.add_song(root / "song.wav")
            self.assertIn("Whisper", pipeline.format_requirements()["F1"])
            self.assertIn("Whisper", pipeline.format_requirements()["F3"])
            self.assertIsNone(pipeline.format_requirements()["F2"])
            pipeline.enqueue(10)
            self.assertTrue({row[4] for row in pipeline.reel_details()} <= {"P", "L", "F4"})
            self.assertEqual(pipeline.posting_queue(), [])

    def test_f1_explains_missing_audio_without_transcribing(self):
        with TemporaryDirectory() as folder, patch(
                "resolve_pipeline.reels.subprocess.check_output", return_value=b""):
            root = Path(folder)
            builder = LocalReelBuilder(hook="Hook", whisper_model=root)
            with self.assertRaisesRegex(NoSpeech, "separate speech source"):
                builder(root / "clip.mp4", root / "song.wav", root / "out.mp4", "F1")
