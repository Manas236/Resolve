import unittest
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from tools.healthcheck import registry_health


class HealthCheckTests(unittest.TestCase):
    def test_command_reports_independent_failures_and_exit_status(self):
        with TemporaryDirectory() as temp:
            report = Path(temp) / "missing report.json"
            result = subprocess.run([sys.executable, "-m", "tools.healthcheck", "--report", str(report)],
                                    capture_output=True, text=True, timeout=55)
            self.assertEqual(result.returncode, 1)
            statuses = json.loads(result.stdout)
            self.assertEqual(set(statuses), {"ffmpeg", "ffprobe", "registry", "chromium"})
            self.assertFalse(statuses["registry"]["ok"])
            self.assertIn(report.name, statuses["registry"]["detail"])

    def test_registry_counts_providers_and_uses_verified_results(self):
        self.assertEqual(registry_health({"provider_summary": {
            "one": {"audio": {"verified": 5}, "video": {"verified": 0}},
            "two": {"audio": {"verified": 0}, "video": {"verified": 2}},
        }}), {"audio": 1, "video": 1})
        self.assertEqual(registry_health({}), {"audio": 0, "video": 0})
        with self.assertRaises(ValueError):
            registry_health({"provider_summary": []})
