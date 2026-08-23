"""Helpers shared by the configuration tests."""

ACCEPTED = []


def expect_all_rejected(loader, configs):
    """Collect every configuration that was wrongly accepted, for the run report."""
    for config in configs:
        try:
            loader(config)
        except ValueError:
            continue
        ACCEPTED.append(config)
