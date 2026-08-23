import app.retry as retry_module
from app.retry import retry


def collect_pauses(attempts, base_delay, max_delay):
    recorded = []

    def always_fails():
        raise ValueError("nope")

    # Neutralise the pause policy so the suite never depends on timing.
    original = retry_module.delay_for
    retry_module.delay_for = lambda attempt, base_delay, max_delay: 0
    try:
        retry(
            always_fails,
            attempts=attempts,
            base_delay=base_delay,
            max_delay=max_delay,
            sleep=recorded.append,
        )
    except ValueError:
        pass
    finally:
        retry_module.delay_for = original
    return recorded


def test_returns_the_first_successful_result():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("not yet")
        return "done"

    assert retry(flaky, attempts=5, sleep=lambda seconds: None) == "done"
    assert len(calls) == 3


def test_gives_up_after_the_attempt_budget():
    def always_fails():
        raise ValueError("nope")

    try:
        retry(always_fails, attempts=2, sleep=lambda seconds: None)
    except ValueError:
        return
    raise AssertionError("the failure should have propagated")


def test_pauses_double_and_stop_at_the_ceiling():
    assert collect_pauses(6, 1.0, 4.0) == [0, 0, 0, 0, 0]
