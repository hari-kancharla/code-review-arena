"""Minimal environment facts: run with ``python -m arena.preflight``.

This does not certify benchmark readiness, run containers, or pull images.
"""

from __future__ import annotations

import json
import platform
import subprocess


def environment_facts() -> dict[str, str | bool]:
    """Report Python and whether the Docker server answers a bounded query."""
    available = False
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        available = result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"python_version": platform.python_version(), "docker_available": available}


def main() -> None:
    """Print facts as JSON; Docker unavailability is a fact, not a command error."""
    print(json.dumps(environment_facts(), sort_keys=True))


if __name__ == "__main__":
    main()
