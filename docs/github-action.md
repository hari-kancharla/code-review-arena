# Continuous Integration

`.github/workflows/ci.yml` runs on every push and pull request. It mirrors the local
commands, so a green checkout matches a green CI run. No credentials are required; the
controls are deterministic.

The backend job installs the package, lints, type-checks, and tests, then validates,
checksum-verifies, and contamination-scans every shipped pack (`v1`, `audit_v1`,
`audit_v2`, and `realfix_seed_v0`), certifies the locally executable packs, and runs
the reference and adversarial control runs:

```yaml
- run: python -m pip install -e ".[dev]"
- run: ruff check arena tests && ruff format --check arena tests
- run: mypy arena
- run: pytest
- run: arena validate benchmark_sets/<pack>          # each shipped pack
- run: arena pack-hash benchmark_sets/<pack>         # fails on a stored-checksum mismatch
- run: arena lint-cases benchmark_sets/<pack> --strict
- run: arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution
- run: arena leaderboard runs/ --metric validated_case_rate --beta 1.0 --include-unverified
```

A Docker job builds the sandbox images and runs the real Docker execution tests. The
execution-verified historical-fix cases live in a separate dataset repository,
[realfix-benchmark](https://github.com/hari-kancharla/realfix-benchmark), whose own CI certifies them against this harness pinned by commit, and
checks its controls: `reference-patch` must validate every case the manifest lists and
`shallow-patch` must validate none. Both checks read the expected count from
`manifest.yaml`, so a pack that gains a case cannot quietly pass a check written for the
old size. Further jobs cover the minimum dependency floors, the packaging smoke
test, the dashboard build (`npm ci` then `npm run build`), and Windows fail-closed
behavior. To benchmark your own reviewer in CI, add a step that runs
`arena run ... --reviewer custom-command --command "..."`.
