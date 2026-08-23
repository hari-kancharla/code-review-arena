from checks import expect_rejected

from app.config_check import load_config

GOOD = {"host": "localhost", "port": 8080, "debug": False}


def test_a_good_configuration_is_returned():
    assert load_config(GOOD) == GOOD


def test_an_unknown_key_is_refused():
    expect_rejected(load_config, {"host": "localhost", "colour": "red"})
