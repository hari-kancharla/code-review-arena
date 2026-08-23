"""Helpers shared by the configuration tests."""


def expect_rejected(loader, config):
    """Raise unless ``loader`` refuses ``config``."""
    try:
        loader(config)
    except ValueError:
        return
    raise AssertionError("this configuration was accepted: " + repr(config))
