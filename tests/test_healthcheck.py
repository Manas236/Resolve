import unittest

from tools.healthcheck import registry_health


class HealthCheckTests(unittest.TestCase):
    def test_registry_counts_providers_and_uses_verified_results(self):
        self.assertEqual(registry_health({"provider_summary": {
            "one": {"audio": {"verified": 5}, "video": {"verified": 0}},
            "two": {"audio": {"verified": 0}, "video": {"verified": 2}},
        }}), {"audio": 1, "video": 1})
        self.assertEqual(registry_health({}), {"audio": 0, "video": 0})
        with self.assertRaises(ValueError):
            registry_health({"provider_summary": []})
