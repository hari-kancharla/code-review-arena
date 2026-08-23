"""Message endpoints."""

from app.limits import TooMany

ALLOWANCE = 2

_SPENT = {}


def send_message(user, text):
    """Send ``text`` on behalf of ``user``."""
    spent = _SPENT.get(user, 0)
    if spent >= ALLOWANCE:
        raise TooMany(user)
    _SPENT[user] = spent + 1
    return "sent"


send_message.reset = _SPENT.clear
