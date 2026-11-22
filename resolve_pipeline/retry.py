"""Bounded retries for idempotent network operations."""

from time import sleep


def retry_read(operation, *, transient, attempts=3, wait=sleep):
    if attempts < 1:
        raise ValueError("Retry attempts must be positive")
    for attempt in range(attempts):
        try:
            return operation()
        except transient as error:
            if attempt + 1 == attempts:
                raise
            delay = getattr(error, "retry_after", None)
            wait(min(60, delay) if delay is not None else 2 ** attempt)
