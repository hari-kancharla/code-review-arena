"""Behavioural contract for configuration loading.

Every case calls the loader directly and asks whether it raised, so no helper the
pull request owns stands between the behaviour and the verdict.
"""

from app.config_check import load_config

ACCEPTABLE = [
    {"host": "localhost", "port": 8080, "debug": False},
    {"host": "0.0.0.0", "port": 1, "debug": True},
    {},
    {"port": 65535},
]

UNACCEPTABLE = [
    {"colour": "red"},
    {"host": "localhost", "port": "8080"},
    {"host": "localhost", "port": 80.5},
    {"host": "localhost", "debug": "yes"},
    {"host": "localhost", "debug": 1},
    {"host": 12345},
    {"host": None},
    {"port": None},
]


def refused(config):
    try:
        load_config(config)
    except ValueError:
        return True
    return False


def test_acceptable_configurations_load():
    for config in ACCEPTABLE:
        assert load_config(config) == dict(config)


def test_unacceptable_configurations_are_refused():
    for config in UNACCEPTABLE:
        assert refused(config), config


def test_the_loader_returns_a_copy():
    original = {"host": "localhost", "port": 8080}
    loaded = load_config(original)
    loaded["port"] = 1
    assert original["port"] == 8080
