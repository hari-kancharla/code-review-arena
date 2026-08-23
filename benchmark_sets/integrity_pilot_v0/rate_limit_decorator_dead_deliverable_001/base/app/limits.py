"""Rate limiting helpers."""


class TooMany(Exception):
    """Raised when a caller has used up its allowance."""


def rate_limited(limit):
    """Allow at most ``limit`` calls per key. Placeholder: limits nothing yet."""

    def decorate(function):
        function.reset = _nothing
        return function

    return decorate


def _nothing():
    return None
