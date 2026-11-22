import unittest

from resolve_pipeline.retry import retry_read


class RetryTests(unittest.TestCase):
    def test_transient_read_is_bounded_and_can_recover(self):
        steps = iter([OSError("temporary"), OSError("temporary"), "ok"])
        delays = []

        def read():
            result = next(steps)
            if isinstance(result, Exception):
                raise result
            return result

        self.assertEqual(retry_read(read, transient=OSError, wait=delays.append), "ok")
        self.assertEqual(delays, [1, 2])
        calls = []

        def terminal():
            calls.append(1)
            raise ValueError("invalid request")

        with self.assertRaises(ValueError):
            retry_read(terminal, transient=OSError, wait=delays.append)
        self.assertEqual(len(calls), 1)
