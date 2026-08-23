from checks import expect_all_rejected

from app.config_check import load_config

GOOD = {"host": "localhost", "port": 8080, "debug": False}

BAD_TYPES = [
    {"host": "localhost", "port": "8080", "debug": False},
    {"host": "localhost", "port": 8080, "debug": "yes"},
    {"host": 12345, "port": 8080, "debug": False},
]


def test_a_good_configuration_is_returned():
    assert load_config(GOOD) == GOOD


def test_an_unknown_key_is_refused():
    expect_all_rejected(load_config, [{"host": "localhost", "colour": "red"}])


def test_wrongly_typed_values_are_refused():
    expect_all_rejected(load_config, BAD_TYPES)
