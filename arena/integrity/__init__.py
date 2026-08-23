"""CRA-Integrity: evaluating whether a reviewer can audit a possibly-gamed PR.

The ordinary Arena track asks whether a reviewer can find a seeded defect. This
track asks a different question: given a pull request whose *own* validation
surface is green, can the reviewer tell that the evidence supporting that green
is no longer trustworthy?

Everything here is additive. The ordinary ``BenchmarkCase`` schema, packs, runner,
scoring, and reports are untouched; integrity work lives in its own models,
loader, executor, certification, reviewers and runner.
"""

from __future__ import annotations

INTEGRITY_TRACK_VERSION = "0.1"
