"""Validate a configuration mapping before the application uses it."""

ALLOWED_KEYS = ("host", "port", "debug")


def problems(config):
    """Return every problem found in ``config``, as readable strings."""
    found = []
    for key in config:
        if key not in ALLOWED_KEYS:
            found.append("unknown key: " + str(key))
    for key, value in config.items():
        message = type_problem(key, value)
        if message:
            found.append(message)
    return found


def type_problem(key, value):
    """Describe the value's type problem, or return None."""
    if key == "port" and not _is_plain_int(value):
        return "port must be a whole number"
    if key == "debug" and not isinstance(value, bool):
        return "debug must be true or false"
    if key == "host" and not isinstance(value, str):
        return "host must be text"
    return None


def _is_plain_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def load_config(config):
    """Return a copy of ``config``, or raise ValueError listing every problem."""
    found = problems(config)
    if found:
        raise ValueError("; ".join(found))
    return dict(config)
