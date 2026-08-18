"""Centralized, documented size and count limits for external input.

These are OPERATIONAL SAFETY bounds (denial-of-service and memory protection),
not claims about semantic correctness. They are chosen with generous headroom
over the maxima observed across the shipped packs, control-reviewer outputs, and
run fixtures, but are deliberately finite so an adversarial pack, reviewer, or API
client cannot drive the harness into pathological memory or compute. External
input that exceeds a bound is REJECTED with a located validation error, never
silently truncated (the one exception is the reviewer source context, whose
truncation is an explicit, recorded part of that contract).

Observed maxima at the time of writing (benchmark_sets/v1, audit_v1, audit_v2):
manifest 451 B, case.yaml 1291 B, pr.diff 1071 B, reference.patch 1021 B,
10 cases/manifest, 1 bug/case, 1 file/bug, 1 line-range/file, 4 concepts,
5 fix keywords, 4 stack entries, title 75, description 201, category 19,
case id 45, concept 26, test_command string 15, YAML depth 9, YAML nodes 104,
test timeout 30 s, beta 1.0, score weights summing to 100, penalties 5/15/20.
Limits below sit well above these.

Scope: these bound individual input byte sizes and (after the YAML structure caps
below) parsed YAML structure. They do NOT bound total filesystem entry count or
provide immutable traversal, name-collision handling, or time-of-check/time-of-use
protection for pack trees; that comprehensive enforcement remains Phase 1C. So
these limits reduce, but do not by themselves eliminate, every adversarial pack's
ability to cause pathological memory or compute.
"""

from __future__ import annotations

# --- Pre-parse raw document byte caps (checked before YAML/JSON parsing) ---
MANIFEST_BYTES = 256 * 1024
CASE_YAML_BYTES = 512 * 1024
DIFF_BYTES = 4 * 1024 * 1024
PATCH_BYTES = 4 * 1024 * 1024

# An individual pack source / test / ground-truth file (read to hash, count
# lines, scan for contamination, or build reviewer context). Generous, but finite.
PACK_FILE_BYTES = 8 * 1024 * 1024
# pack.sha256 is a single hex digest line.
CHECKSUM_FILE_BYTES = 256

# --- Immutable pack snapshot caps (Phase 1C) ---
# Whole-pack bounds for secure source -> snapshot copy, enforced before excessive
# memory or disk use. Observed across the shipped packs: <=84 files, <=74 dirs,
# <=40 KB total, depth <=9; these sit far above with operational headroom.
SNAPSHOT_MAX_FILES = 4096
SNAPSHOT_MAX_DIRS = 4096
SNAPSHOT_MAX_TOTAL_BYTES = 256 * 1024 * 1024
SNAPSHOT_MAX_DEPTH = 32
SNAPSHOT_MANIFEST_ENTRIES = SNAPSHOT_MAX_FILES
# Total directory entries (files + dirs) enumerated, enforced WHILE iterating each
# directory so an enormous name list cannot be materialized before rejection.
SNAPSHOT_MAX_ENTRIES = SNAPSHOT_MAX_FILES + SNAPSHOT_MAX_DIRS

# --- Parsed-YAML structure caps (checked during parsing, after the byte cap) ---
# Bound parser amplification that a byte cap alone misses (deep nesting, huge node
# counts). Observed: depth 9, nodes 104 across the shipped packs; these are far above.
YAML_MAX_DEPTH = 64
YAML_MAX_NODES = 100_000

# --- Parsed-JSON structure caps for reviewer output (complement the byte cap) ---
# The RAW_RESPONSE_BYTES ceiling is the first boundary; these bound the parsed
# structure so a within-byte-limit document cannot be pathologically deep or dense.
# A valid ReviewResult is a few thousand nodes at most; these sit far above.
JSON_MAX_DEPTH = 64
JSON_MAX_NODES = 200_000

# --- Persisted reviewer parse-evidence caps ---
PARSE_ACTIONS_MAX = 16  # recorded salvage actions per response (fixed vocabulary)
PARSE_ACTION_LEN = 64  # one action identifier
PARSE_ERROR_SUMMARY_LEN = 512  # bounded failure reason; never the full reviewer output

# --- Pack structure counts ---
CASES_PER_MANIFEST = 1024
BUGS_PER_CASE = 50  # also the finding-to-bug matching cap (MAX_BUGS_PER_CASE)
FILES_PER_BUG = 128
LINE_RANGES_PER_FILE = 256
ACCEPTABLE_FINDINGS_PER_CASE = 128
STACK_ENTRIES = 32
CONCEPTS_PER_BUG = 128
MUST_MENTION_PER_BUG = 128
FIX_KEYWORDS_PER_BUG = 128
PROTECTED_PATHS_PER_CASE = 512
STRUCTURAL_VALIDATORS_PER_CASE = 64
ARGV_COMMANDS = 32
ARGV_TOKENS = 256

# --- Pack string-length caps ---
TITLE_LEN = 1024
DESCRIPTION_LEN = 16 * 1024
CATEGORY_LEN = 128
IDENTIFIER_LEN = 128  # case ids; matches the case-id length bound in security.paths
SUMMARY_LEN = 4 * 1024
CONCEPT_LEN = 256
PHRASE_LEN = 256  # must_mention / fix keywords / structural validator names
TOKEN_LEN = 4096  # one argv token
COMMAND_STRING_LEN = 16 * 1024  # a test_command / static_analysis_command string
DOCKER_IMAGE_REF_LEN = 512
LINE_NUMBER_MAX = 10_000_000  # bound line-number magnitude (start/end)

# --- Numeric domain limits (domain-meaningful safety bounds, not correctness) ---
# Each is chosen for its own domain rather than reusing an unrelated magnitude cap.
# Observed shipped values: timeout <= 30 s, beta 1.0, weights sum to 100, penalties
# 5/15/20. External input outside a range is rejected, never clamped.
TEST_TIMEOUT_SECONDS_MAX = 86_400  # 24 h ceiling on a single test command
REVIEWER_TIMEOUT_SECONDS_MAX = 86_400  # 24 h ceiling on a single reviewer invocation
API_WALL_SECONDS_MAX = 86_400  # 24 h ceiling on a run wall-clock budget
API_COST_MAX = 1_000_000  # USD ceiling on a run cost budget
SCORE_WEIGHT_MAX = 100  # one score weight (the six weights sum to 100)
PENALTY_MAX = 100  # a scoring penalty and the false-positive penalty cap
BETA_MAX = 100  # F-beta beta

# --- Reviewer-visible input (mirrors ContextLimits; see arena/benchmark/case_loader) ---
RELEVANT_FILE_COUNT = 40
RELEVANT_FILE_BYTES = 64 * 1024
RELEVANT_FILES_TOTAL_BYTES = 256 * 1024
OMITTED_FILE_COUNT = 4096
TEST_OUTPUT_BYTES = 512 * 1024
STATIC_ANALYSIS_OUTPUT_BYTES = 512 * 1024

# --- Git-authoritative patch application (Phase 1D) ---
# A single git plumbing invocation is short-lived and childless; these bound its
# wall time and output. The changed-file cap mirrors the snapshot file cap.
# --- Historical-fix importer ---
# Per-file materialization and diff output are read through the bounded Git runner,
# so a single object is capped at GIT_OUTPUT_BYTES; whole-import file/byte counts
# reuse the snapshot caps and are enforced INCREMENTALLY while blobs are loaded, so
# an oversized selection is rejected before it is all read into memory. Source label
# is a stable "owner/repo"-style string.
IMPORT_MAX_FILES = SNAPSHOT_MAX_FILES
IMPORT_MAX_TOTAL_BYTES = SNAPSHOT_MAX_TOTAL_BYTES
IMPORT_MAX_FILE_BYTES = PACK_FILE_BYTES
IMPORT_SOURCE_PATHS = 256
IMPORT_SOURCE_LABEL_LEN = 256

# --- Training-data exposure disclosure (see arena/benchmark/exposure.py) ---
# The smallest cohort that may carry a published pre/post difference. At five
# cases a difference is noise; raising this to make a number appear is a defect,
# not a tuning change, and tests/test_exposure.py pins it.
MIN_COHORT_CASES = 8
# Symmetric guard band, in days, around a declared knowledge cutoff. Cases inside
# it join neither cohort. Crawl-to-train lag, backports and post-fix discussion
# make a hard boundary indefensible. The CLI allows this to be WIDENED (more
# conservative) but never narrowed.
DEFAULT_CUTOFF_GRACE_DAYS = 90
MODEL_CUTOFF_SOURCE_LEN = 512
UPSTREAM_REF_LEN = 512

GIT_TIMEOUT_SECONDS = 60  # default per-invocation wall-clock budget
GIT_TIMEOUT_SECONDS_MIN = 1
GIT_TIMEOUT_SECONDS_MAX = 3600
GIT_OUTPUT_BYTES = 4 * 1024 * 1024  # per-stream cap, enforced WHILE reading
GIT_MAX_CHANGED_FILES = SNAPSHOT_MAX_FILES

# --- Reviewer output ---
RAW_RESPONSE_BYTES = 2 * 1024 * 1024
FINDINGS_PER_RESPONSE = 200  # MAX_FINDINGS_PER_RESPONSE
FINDING_TITLE_LEN = 1024
FINDING_SUMMARY_LEN = 8 * 1024
EVIDENCE_LEN = 16 * 1024
SUGGESTED_FIX_LEN = 64 * 1024
PATCH_LEN = 4 * 1024 * 1024  # per-finding suggested_patch and case-level proposed_patch
REPLACEMENT_CODE_LEN = 256 * 1024
REVIEW_SUMMARY_LEN = 16 * 1024
TOOL_USAGE_ENTRIES = 256
TOOL_USAGE_ENTRY_LEN = 1024

# --- API request ---
API_REQUEST_BODY_BYTES = 256 * 1024
BENCHMARK_SET_NAME_LEN = 256
REVIEWER_ID_LEN = 256
COMMAND_LEN = 16 * 1024
MODEL_ID_LEN = 256
