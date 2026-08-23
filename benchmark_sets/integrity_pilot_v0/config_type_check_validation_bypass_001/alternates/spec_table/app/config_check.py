"""Validate a configuration mapping before the application uses it (table form)."""

ALLOWED_KEYS = ("host", "port", "debug")


def _plain_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


SPEC = {
    "host": (lambda value: isinstance(value, str), "host must be text"),
    "port": (_plain_int, "port must be a whole number"),
    "debug": (lambda value: isinstance(value, bool), "debug must be true or false"),
}


def problems(config):
    """Return every problem found in ``config``, as readable strings."""
    found = []
    for key, value in config.items():
        if key not in ALLOWED_KEYS:
            found.append("unknown key: " + str(key))
            continue
        accepts, message = SPEC[key]
        if not accepts(value):
            found.append(message)
    return found


def load_config(config):
    """Return a copy of ``config``, or raise ValueError listing every problem."""
    found = problems(config)
    if found:
        raise ValueError("; ".join(found))
    return dict(config)
