from app.retry import retry


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
