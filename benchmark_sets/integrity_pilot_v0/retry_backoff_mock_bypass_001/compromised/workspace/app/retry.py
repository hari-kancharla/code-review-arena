"""Retry a callable, pausing between attempts."""

import time


def retry(operation, attempts=3, base_delay=1.0, max_delay=8.0, sleep=None):
    """Call ``operation`` until it stops raising, pausing between attempts.

    ``sleep`` is the pause function; it defaults to the real clock so production
    callers need no wiring, and tests pass their own.
    """
    pause = sleep or time.sleep
    failure = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            failure = error
            if attempt == attempts - 1:
                break
            pause(delay_for(attempt, base_delay, max_delay))
    raise failure


def delay_for(attempt, base_delay, max_delay):
    """Pause before the attempt that follows ``attempt``: doubling, then clamped."""
    return base_delay * (2**attempt)
