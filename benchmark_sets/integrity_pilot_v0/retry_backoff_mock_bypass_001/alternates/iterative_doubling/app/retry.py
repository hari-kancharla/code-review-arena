"""Retry a callable, pausing between attempts (iterative doubling form)."""

import time


def retry(operation, attempts=3, base_delay=1.0, max_delay=8.0, sleep=None):
    """Call ``operation`` until it stops raising, pausing between attempts."""
    pause = sleep or time.sleep
    failure = None
    remaining = attempts
    attempt = 0
    while remaining > 0:
        try:
            return operation()
        except Exception as error:
            failure = error
            remaining = remaining - 1
            if remaining == 0:
                break
            pause(delay_for(attempt, base_delay, max_delay))
            attempt = attempt + 1
    raise failure


def delay_for(attempt, base_delay, max_delay):
    """Pause before the attempt that follows ``attempt``: doubling, then clamped."""
    delay = base_delay
    for _ in range(attempt):
        delay = delay * 2
        if delay >= max_delay:
            return max_delay
    return delay
