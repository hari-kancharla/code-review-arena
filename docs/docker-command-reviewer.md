# Containerized Reviewer (docker-command)

The `docker-command` reviewer runs an external review command inside a hardened
Docker container: no network, all capabilities dropped, no-new-privileges,
read-only root filesystem, non-root user, CPU/memory/PID limits, and no image
pulls. It reuses the same hardened invocation as the Docker test backend
(`arena/execution/test_executor.py`), and the reviewer payload is mounted
**read-only** at `/workspace`.

Unlike `custom-command` — a host process that can always read every pack's
`reference.patch` — a `docker-command` review is a **blind** measurement: runs
record `reviewer_oracle_reachable: false` and are eligible for the default
leaderboard policy (Docker-backed, full coverage, externally verified pack,
exact output).

## Example

```bash
# Build or vet your reviewer image first; it is never pulled.
arena run benchmark_sets/audit_v1 \
  --reviewer docker-command \
  --docker-image my-reviewer:1 \
  --command "python /opt/reviewer/wrapper.py --case {case_json}" \
  --mode full
```

## What the container receives

The container sees ONLY the payload directory, mounted read-only at `/workspace`:

| Path | Content |
|---|---|
| `/workspace/case.json` | The blind reviewer JSON (same payload as `custom-command`) |
| `/workspace/pr.diff` | The PR diff |
| `/workspace/workspace/...` | The bounded relevant files |

`{case_json}`, `{diff_file}`, and `{workspace}` expand to these container paths;
`{case_id}` expands to the case id. Host paths are never placed on the command
line, and template quoting is parsed before substitution (spaces and quotes in
values stay single arguments).

## Requirements and limits

- `--docker-image` is required and must exist locally; the container is never
  pulled. The executor skips Docker runs for images it does not have rather
  than pulling unvetted code.
- The image is in the trust boundary: it must not contain answers for known
  case ids, and it must provide any interpreter the command names. The
  operator builds or vets it, exactly like the benchmark sandbox image.
- No host environment or credentials enter the container. A wrapper needing a
  model API should call an endpoint configured inside the image.
- The API server treats `docker-command` like `custom-command`: it requires the
  local-execution opt-in (`ARENA_SERVER_ALLOW_LOCAL_EXECUTION`).
- The container runs as a non-root user mapped to the caller; the payload
  directory is cleaned up after every review.

## Output contract

Identical to `custom-command`: stdout must contain a single JSON document
matching the standard `ReviewResult` schema; nonzero exits, timeouts, and
invalid JSON are recorded as structured invalid reviewer output.
