"""Rate limiting helpers (callable-object form)."""


class TooMany(Exception):
    """Raised when a caller has used up its allowance."""


class _Limiter:
    def __init__(self, function, limit):
        self._function = function
        self._limit = limit
        self._used = {}

    def __call__(self, key, *args, **kwargs):
        spent = self._used.get(key, 0)
        if spent >= self._limit:
            raise TooMany(key)
        self._used[key] = spent + 1
        return self._function(key, *args, **kwargs)

    def reset(self):
        self._used.clear()


def rate_limited(limit):
    """Allow at most ``limit`` calls per key, the key being the first argument."""

    def decorate(function):
        return _Limiter(function, limit)

    return decorate
