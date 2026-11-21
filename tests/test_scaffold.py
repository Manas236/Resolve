import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from resolve_pipeline.config import Config


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env_file = self.root / ".env"

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
                self.assertEqual(marker.read_text(), "preserved")
            marker.write_text("preserved")

    def test_environment_overrides_file_relative_to_its_location(self):
        self.env_file.write_text("RESOLVE_STATE_DIR=from-file\n")
        config = Config.load(self.env_file, {"RESOLVE_STATE_DIR": "from-environment"})
        self.assertEqual(config.state_dir, self.root / "from-environment")

    def test_invalid_configuration_exits_without_creating_state(self):
        result = self.run_cli(RESOLVE_LOG_LEVEL="bogus")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Invalid RESOLVE_LOG_LEVEL", result.stderr)
        self.assertFalse((self.root / "state").exists())

    def test_malformed_file_does_not_leak_value(self):
        self.env_file.write_text("unparsed-sensitive-value\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("line 1", result.stderr)
        self.assertNotIn("unparsed-sensitive-value", result.stderr)

    def test_unusable_state_path_fails(self):
        (self.root / "state").touch()
        self.assertEqual(self.run_cli().returncode, 1)

    def test_duplicate_settings_fail(self):
        self.env_file.write_text("RESOLVE_LOG_LEVEL=INFO\nRESOLVE_LOG_LEVEL=ERROR\n")
        with self.assertRaisesRegex(ValueError, "Duplicate environment key on line 2"):
            Config.load(self.env_file, {})
