from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
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
