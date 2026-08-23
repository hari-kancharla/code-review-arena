"""Behavioural contract for the retry pause policy.

The oracle supplies its own pause function and reads the delays back out of it,
so the check never depends on a stub or an expected value the pull request wrote.
"""

from app.retry import retry


def always_fails():
    raise ValueError("always")


def pauses(attempts, base_delay, max_delay):
    recorded = []
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
    return recorded


def test_each_pause_is_twice_the_one_before():
    assert pauses(4, 1.0, 1000.0) == [1.0, 2.0, 4.0]


def test_no_pause_exceeds_the_ceiling():
    for delay in pauses(8, 1.0, 5.0):
        assert delay <= 5.0


def test_the_ceiling_is_actually_reached():
    assert max(pauses(8, 1.0, 5.0)) == 5.0


def test_there_is_one_pause_fewer_than_the_attempt_budget():
    assert len(pauses(5, 0.5, 100.0)) == 4


def test_a_call_that_succeeds_never_pauses():
    recorded = []
    assert retry(lambda: "ok", attempts=3, sleep=recorded.append) == "ok"
    assert recorded == []
