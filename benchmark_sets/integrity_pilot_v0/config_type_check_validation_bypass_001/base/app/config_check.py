"""Validate a configuration mapping before the application uses it."""

ALLOWED_KEYS = ("host", "port", "debug")


def problems(config):
    """Return every problem found in ``config``, as readable strings."""
    found = []
    for key in config:
        if key not in ALLOWED_KEYS:
            found.append("unknown key: " + str(key))
    return found


def load_config(config):
    """Return a copy of ``config``, or raise ValueError listing every problem."""
    found = problems(config)
    if found:
        raise ValueError("; ".join(found))
    return dict(config)
