from app.api import send_message
from app.limits import TooMany


def test_a_message_is_sent():
    send_message.reset()
    assert send_message("ann", "hello") == "sent"


def test_a_user_may_send_up_to_the_allowance():
    send_message.reset()
    assert send_message("ann", "one") == "sent"
    assert send_message("ann", "two") == "sent"


def test_a_send_beyond_the_allowance_is_refused():
    send_message.reset()
    send_message("ann", "one")
    send_message("ann", "two")
    try:
        send_message("ann", "three")
    except TooMany:
        return
    raise AssertionError("the third send should have been refused")


def test_each_user_has_their_own_allowance():
    send_message.reset()
    send_message("ann", "one")
    send_message("ann", "two")
    assert send_message("bob", "hi") == "sent"
