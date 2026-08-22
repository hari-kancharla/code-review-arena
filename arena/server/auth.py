"""Optional token authentication and execution opt-ins for the API server.

The server is meant for local and trusted-network use. It is not safe to
expose publicly: set ARENA_API_TOKEN to require a token on run creation, and
leave ARENA_SERVER_ALLOW_LOCAL_EXECUTION unset so HTTP callers can neither run
a local command (fixture test commands, custom-command reviewers) nor make the
server call out to a URL they chose (openai:/http: reviewers).
"""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException


def server_local_execution_enabled() -> bool:
    return os.getenv("ARENA_SERVER_ALLOW_LOCAL_EXECUTION", "").lower() in {"1", "true", "yes"}


# Reviewer specs that run entirely in-process: they spawn nothing and open no
# outbound connection, so an HTTP caller may construct them without the opt-in.
_IN_PROCESS_REVIEWERS = frozenset(
    {
        "reference-patch",
        "control:reference_patch",
        "shallow-patch",
        "control:shallow_patch",
        "control",
        "mock",
    }
)


def reviewer_needs_execution_optin(spec: str, command: str | None = None) -> bool:
    """Whether building this reviewer leaves the process, and so needs the opt-in.

    An allowlist rather than a denylist: only the built-in in-process reviewers
    are safe for an unauthenticated caller (token auth is off unless
    ARENA_API_TOKEN is set) to construct. Everything else is gated --
    ``custom-command`` spawns a caller-supplied process, and ``openai:``/``http:``
    make the *server* issue outbound requests to a caller-supplied URL, which is
    server-side request forgery against localhost services or a cloud metadata
    endpoint. A reviewer type added later is gated by default until it is
    deliberately listed above.
    """
    if command:
        return True
    if spec in _IN_PROCESS_REVIEWERS:
        return False
    return not spec.startswith(("control:", "mock:"))


def require_api_token(
    x_arena_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Reject the request when ARENA_API_TOKEN is set and not presented."""
    expected = os.getenv("ARENA_API_TOKEN")
    if not expected:
        return
    provided = x_arena_token
    if provided is None and authorization and authorization.lower().startswith("bearer "):
        provided = authorization[len("bearer ") :]
    if provided is None or not _tokens_match(provided, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid API token")


def _tokens_match(provided: str, expected: str) -> bool:
    """Constant-time compare that tolerates any header bytes.

    ``secrets.compare_digest`` refuses str operands containing non-ASCII, and
    Starlette decodes header bytes as latin-1, so a token header carrying any
    byte above 0x7F would raise TypeError and surface as a 500 instead of a 401 --
    and a non-ASCII ARENA_API_TOKEN would break every request, including a
    correct one. Comparing the encoded bytes keeps the comparison constant-time
    while accepting arbitrary input.
    """
    return secrets.compare_digest(
        provided.encode("utf-8", "surrogateescape"),
        expected.encode("utf-8", "surrogateescape"),
    )
