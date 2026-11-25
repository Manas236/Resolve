import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from resolve_pipeline.config import Config


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env_file = self.root / ".env"

    def test_sample_cli_uses_process_settings_over_env_file(self):
        from tools import render_format_samples as samples
        self.env_file.write_text("RESOLVE_WHISPER_MODEL=file-model", encoding="utf-8")
        args = ["samples", "--env-file", str(self.env_file), "--state", str(self.root),
                "--video", "video.mp4", "--song", "song.mp3", "--formats", "P"]
        with patch.object(sys, "argv", args), patch.dict(os.environ, {"RESOLVE_WHISPER_MODEL": "pc-model"}), \
                patch.object(samples, "configured_builder") as builder, patch.object(samples, "Pipeline") as factory:
            pipeline = factory.return_value
            pipeline.make_now.return_value = [1]
            pipeline.list_reels.return_value = [(1, "review", None)]
            self.assertEqual(samples.main(), 0)
            self.assertEqual(builder.call_args.args[0]["RESOLVE_WHISPER_MODEL"], "pc-model")

    def run_cli(self, **values):
        env = {key: value for key, value in os.environ.items() if not key.startswith("RESOLVE_")}
        return subprocess.run(
            [sys.executable, "-m", "resolve_pipeline", "--env-file", str(self.env_file)],
            env=env | values,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_normal_startup_and_restart_preserve_local_state(self):
        for _ in range(2):
            result = self.run_cli()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Runtime directories ready", result.stderr)
            state = self.root / "state"
            self.assertEqual({item.name for item in state.iterdir()}, {"downloads", "media", "cache"})
            marker = state / "media" / "existing"
            if marker.exists():
                self.assertEqual(marker.read_text(encoding="utf-8"), "preserved")
            marker.write_text("preserved", encoding="utf-8")

    def test_environment_overrides_file_relative_to_its_location(self):
        self.env_file.write_text("RESOLVE_STATE_DIR=from-file\n", encoding="utf-8")
        config = Config.load(self.env_file, {"RESOLVE_STATE_DIR": "from-environment"})
        self.assertEqual(config.state_dir, self.root / "from-environment")

    def test_invalid_configuration_exits_without_creating_state(self):
        result = self.run_cli(RESOLVE_LOG_LEVEL="bogus")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Invalid RESOLVE_LOG_LEVEL", result.stderr)
        self.assertFalse((self.root / "state").exists())

    def test_malformed_file_does_not_leak_value(self):
        self.env_file.write_text("unparsed-sensitive-value\n", encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("line 1", result.stderr)
        self.assertNotIn("unparsed-sensitive-value", result.stderr)

    def test_unusable_state_path_fails(self):
        (self.root / "state").touch()
        self.assertEqual(self.run_cli().returncode, 1)

    def test_duplicate_settings_fail(self):
        self.env_file.write_text("RESOLVE_LOG_LEVEL=INFO\nRESOLVE_LOG_LEVEL=ERROR\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate environment key on line 2"):
            Config.load(self.env_file, {})
