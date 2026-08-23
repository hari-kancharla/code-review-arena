# Audit Report

`arena audit-report` aggregates real run JSON from `runs/` for a benchmark pack and writes:

- Markdown report (default: `docs/reports/audit-v1-results.md`)
- Dashboard JSON (only when `--json-output` names a path)

`--json-output` has no default. Writing `dashboard/public/reports/audit-v1.json` is
publishing to a tracked, committed file, so it has to be asked for explicitly rather
than happening as a side effect of generating a report locally.

It defaults to `audit_v1`; pass `--benchmark-set` to aggregate another pack (and point
`--json-output` at that pack's dashboard file):

```bash
arena audit-report runs/ --output docs/reports/audit-v1-results.md
arena audit-report runs/ --output docs/reports/audit-v1-results.md \
  --json-output dashboard/public/reports/audit-v1.json
arena audit-report runs/ --benchmark-set audit_v2 \
  --output docs/reports/audit-v2-results.md \
  --json-output dashboard/public/reports/audit-v2.json
```

The command never invents metrics. When no runs for the pack exist, it writes a clear empty-state report.

## Run admissibility

Only runs the harness stamped `run_status=complete` on the v2 schema are aggregated.
A run marked `invalid` — a pack whose content no longer matches its checksum, a run
that produced no results, or one that needed test execution it never got — is counted
in `summary.excluded_run_count`, reported on stderr, and never contributes a
number to the report. This is the same integrity floor `arena leaderboard` applies, so
a run too untrustworthy to rank cannot be promoted into a published artifact instead.

Each reviewer row carries the `run_status` and `pack_checksum_verified` of the run
behind it, so a published figure can be traced back to the trust state that produced it.

## Sections

1. Summary
2. Methodology
3. Reviewer comparison (`detection_f_beta` vs `validated_case_rate`)
4. Detection vs validation gap
5. Failure mode breakdown
6. Case studies (up to three failing examples)
7. Reproducibility commands
8. Limitations

View the rendered dashboard page at `/reports/audit-v1` after generating the JSON file.
