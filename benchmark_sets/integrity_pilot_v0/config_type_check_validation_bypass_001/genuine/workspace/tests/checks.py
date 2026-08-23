"""Helpers shared by the configuration tests."""


def expect_all_rejected(loader, configs):
    """Raise once, naming every configuration that was wrongly accepted."""
    accepted = []
    for config in configs:
        try:
            loader(config)
        except ValueError:
            continue
        accepted.append(config)
    if accepted:
        raise AssertionError("these configurations were accepted: " + repr(accepted))
