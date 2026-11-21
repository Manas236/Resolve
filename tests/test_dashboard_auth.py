import unittest

from resolve_pipeline.dashboard_auth import DashboardAuth, password_hash


class DashboardAuthTests(unittest.TestCase):
    def test_lockout_expiry_login_and_logout(self):
        now = 100
        salt = b"test salt 16 bytes"
        auth = DashboardAuth(salt.hex() + "$" + password_hash("correct", salt), clock=lambda: now)
        for _ in range(5):
            self.assertIsNone(auth.login("wrong"))
        self.assertIsNone(auth.login("correct"))
        now += 60
        session = auth.login("correct")
        self.assertTrue(auth.valid(session))
        auth.logout(session)
        self.assertFalse(auth.valid(session))
        session = auth.login("correct")
        now += 8 * 3600
        self.assertFalse(auth.valid(session))
