from app.api import send_message


def test_a_message_is_sent():
    assert send_message("ann", "hello") == "sent"
