"""Message endpoints."""

from app.limits import rate_limited

ALLOWANCE = 2


@rate_limited(ALLOWANCE)
def send_message(user, text):
    """Send ``text`` on behalf of ``user``."""
    return "sent"
