"""Reduced-privilege environment and resource limits for fixture commands.

Benchmark fixtures are untrusted: packs can come from third parties, and the
test commands they declare run on the host (when trusted-local execution is
allowed). Three mitigations apply to every locally executed fixture command:

- the child environment is built from an allowlist instead of inheriting the
  parent environment, so shell secrets and API keys are never exposed;
- HOME and TMPDIR point at a fresh, empty, per-run temporary directory, so a
  fixture cannot read ~/.ssh, ~/.aws, ~/.config, or the host temp space.
  Isolated HOME would also hide a ``pip install --user`` site, so the harness
  interpreter's user-site is restored on PYTHONPATH (with PYTHONNOUSERSITE=1)
  and pytest stays importable; parent PYTHONPATH is still not forwarded;
- POSIX resource limits bound CPU time, file size, open files, and process
  count so a runaway or malicious fixture cannot exhaust the host.

None of this replaces real isolation (containers): it is containment, not a
security boundary. Use the Docker backend for untrusted packs.
"""

from __future__ import annotations

import os
import site
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

# Variables forwarded from the parent environment when present. HOME and the
# temp-dir vars are deliberately excluded: they are replaced with an isolated
# per-run directory (see sandboxed_home_env). Everything else requires an
# explicit opt-in via ARENA_PASSTHROUGH_ENV.
SAFE_ENV_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
)


def harness_user_site() -> str | None:
    """Return this interpreter's user-site directory, if it is in use.

    Isolated HOME would otherwise hide ``~/.local/.../site-packages``, so a
    ``pip install --user`` pytest (common when system site-packages is not
    writable) becomes ``No module named pytest``. Computed in the parent
    before HOME is rewritten.
    """
    if not site.ENABLE_USER_SITE:
        return None
    user_site = site.getusersitepackages()
    if isinstance(user_site, str) and Path(user_site).is_dir():
        return user_site
    return None


def sandbox_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Minimal allowlisted environment for fixture subprocesses.

    ``ARENA_PASSTHROUGH_ENV`` may name a comma-separated list of additional
    variables to forward explicitly (e.g. a proxy setting a pack's fixtures
    legitimately need). The parent environment is never forwarded wholesale.
    Isolated HOME hides user-site, so the harness interpreter's user-site is
    placed on PYTHONPATH and PYTHONNOUSERSITE is set; parent PYTHONPATH is
    not inherited.
    """
    env = {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}
    passthrough = os.getenv("ARENA_PASSTHROUGH_ENV", "")
    for name in filter(None, (item.strip() for item in passthrough.split(","))):
        if name in os.environ:
            env[name] = os.environ[name]
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    if extra:
        env.update(extra)
    # Isolated HOME hides user-site. Restore it on PYTHONPATH without inheriting
    # the parent PYTHONPATH (unless the caller opted in via passthrough/extra).
    user_site = harness_user_site()
    if user_site:
        existing = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
        if user_site not in existing:
            env["PYTHONPATH"] = os.pathsep.join([user_site, *existing])
    return env


@contextmanager
def sandboxed_home_env(extra: dict[str, str] | None = None) -> Iterator[dict[str, str]]:
    """Allowlisted env with an isolated, empty HOME/TMPDIR for the child.

    A fresh temporary directory becomes the fixture's HOME and temp space, then is
    removed when the command finishes. Containment, not isolation (see the module
    docstring) -- it stops casual credential reads, not a determined escape.
    """
    with tempfile.TemporaryDirectory(prefix="arena-home-") as home:
        env = sandbox_env(extra=extra)
        env.update({"HOME": home, "TMPDIR": home, "TEMP": home, "TMP": home})
        yield env


def _clamped(limit: int, desired: int) -> tuple[int, int]:
    import resource

    _soft, hard = resource.getrlimit(limit)
    cap = desired if hard == resource.RLIM_INFINITY else min(desired, hard)
    return (cap, cap)


def resource_limiter(cpu_seconds: int) -> Callable[[], None] | None:
    """Return a preexec_fn applying rlimits to the child, or None off-POSIX.

    Limits are env-overridable: ARENA_RLIMIT_AS_BYTES, ARENA_RLIMIT_FSIZE_BYTES,
    ARENA_RLIMIT_NOFILE, ARENA_RLIMIT_NPROC. The CPU ceiling follows the
    command's wall-clock timeout. Children inherit the limits, so forked
    helpers stay bounded too.
    """
    if sys.platform == "win32":
        return None
    address_space = int(os.getenv("ARENA_RLIMIT_AS_BYTES", str(4 * 1024**3)))
    file_size = int(os.getenv("ARENA_RLIMIT_FSIZE_BYTES", str(256 * 1024**2)))
    open_files = int(os.getenv("ARENA_RLIMIT_NOFILE", "512"))
    processes = int(os.getenv("ARENA_RLIMIT_NPROC", "2048"))

    def set_limits() -> None:
        import resource

        resource.setrlimit(
            resource.RLIMIT_CPU, _clamped(resource.RLIMIT_CPU, max(cpu_seconds, 1) + 5)
        )
        resource.setrlimit(resource.RLIMIT_FSIZE, _clamped(resource.RLIMIT_FSIZE, file_size))
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, _clamped(resource.RLIMIT_NOFILE, open_files))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, _clamped(resource.RLIMIT_NPROC, processes))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_AS, _clamped(resource.RLIMIT_AS, address_space))
        except (ValueError, OSError):
            # macOS rejects RLIMIT_AS in some configurations; the other limits
            # still apply.
            pass

    return set_limits
