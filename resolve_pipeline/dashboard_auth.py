"""Single-operator dashboard authentication."""

from hashlib import scrypt
from hmac import compare_digest
from secrets import token_urlsafe
from threading import Lock
from time import monotonic


def password_hash(password, salt):
    return scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()


class DashboardAuth:
    def __init__(self, encoded, *, clock=monotonic):
        try:
            salt, digest = encoded.split("$")
            self.salt = bytes.fromhex(salt)
            self.digest = bytes.fromhex(digest)
            if len(self.salt) < 16 or len(self.digest) != 64:
                raise ValueError
        except ValueError as error:
            raise ValueError("Expected dashboard password hash as salt_hex$digest_hex") from error
        self.clock = clock
        self.lock = Lock()
        self.failures = 0
        self.locked_until = 0
        self.session = None
        self.expires = 0

    def login(self, password):
        with self.lock:
            now = self.clock()
            if now < self.locked_until:
                return None
            if not compare_digest(bytes.fromhex(password_hash(password, self.salt)), self.digest):
                self.failures += 1
                if self.failures == 5:
                    self.locked_until = now + 60
                    self.failures = 0
                return None
            self.failures = 0
            self.session = token_urlsafe(32)
            self.expires = now + 8 * 3600
            return self.session

    def valid(self, value):
        with self.lock:
            return (self.session is not None and self.clock() < self.expires
                    and compare_digest(value.encode(), self.session.encode()))

    def logout(self, value):
        with self.lock:
            if self.session is not None and compare_digest(value.encode(), self.session.encode()):
                self.session = None
