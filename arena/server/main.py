"""FastAPI application entrypoint.

Intended for local or trusted-network use; do not expose publicly as-is.
Set ARENA_API_TOKEN to require a token on run creation, and leave
ARENA_SERVER_ALLOW_LOCAL_EXECUTION unset so HTTP callers can neither trigger
local command execution nor steer the server into calling a URL they chose.
"""

from fastapi import FastAPI

from arena import __version__
from arena.core import limits
from arena.server.middleware import BodySizeLimitMiddleware
from arena.server.routes import cases, leaderboard, runs

app = FastAPI(title="CodeReview Arena API", version=__version__)
# Bound request-body bytes before Starlette buffers/parses them (pre-parse memory cap).
app.add_middleware(BodySizeLimitMiddleware, max_bytes=limits.API_REQUEST_BODY_BYTES)
app.include_router(cases.router)
app.include_router(runs.router)
app.include_router(leaderboard.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
