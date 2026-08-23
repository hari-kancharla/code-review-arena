"""Rate limiting helpers."""

import functools


class TooMany(Exception):
    """Raised when a caller has used up its allowance."""


def rate_limited(limit):
    """Allow at most ``limit`` calls per key, the key being the first argument."""

    def decorate(function):
        used = {}

        @functools.wraps(function)
        def wrapper(key, *args, **kwargs):
            spent = used.get(key, 0)
            if spent >= limit:
                raise TooMany(key)
            used[key] = spent + 1
            return function(key, *args, **kwargs)

        wrapper.reset = used.clear
        return wrapper

    return decorate
