"""Behavioural contract for the reusable rate-limiting decorator.

The contract decorates its own functions, so it measures the deliverable itself
rather than whatever the endpoint happens to do internally.
"""

from app.limits import TooMany, rate_limited


def build(limit):
    @rate_limited(limit)
    def action(key, value=None):
        return ("ok", key, value)

    return action


def refused(action, key):
    try:
        action(key)
    except TooMany:
        return True
    return False


def test_each_key_gets_its_own_allowance():
    action = build(2)
    assert action("a")[0] == "ok"
    assert action("a")[0] == "ok"
    assert refused(action, "a")
    assert action("b")[0] == "ok"


def test_two_decorated_functions_do_not_share_an_allowance():
    first = build(1)
    second = build(1)
    assert first("k")[0] == "ok"
    assert second("k")[0] == "ok"


def test_the_wrapped_call_still_receives_its_arguments():
    action = build(5)
    assert action("k", value=7) == ("ok", "k", 7)


def test_resetting_restores_the_allowance():
    action = build(1)
    action("k")
    assert refused(action, "k")
    action.reset()
    assert action("k")[0] == "ok"
