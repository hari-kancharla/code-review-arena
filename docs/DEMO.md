# Demo: fresh clone to a rendered leaderboard

Every command below was run to produce this file. It takes a few minutes and needs
Python 3.11+ and Node 20+. No credentials are required. The controls are deterministic.

## 1. Install the CLI

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## 2. Confirm the packs are valid

```bash
arena validate benchmark_sets/v1
arena validate benchmark_sets/audit_v1
arena validate benchmark_sets/audit_v2
arena validate benchmark_sets/realfix_seed_v0
```

Each prints `Valid benchmark set` and exits 0.

## 3. Generate run evidence

`--allow-local-execution` runs the fixture-owned tests in copied workspaces; it is off
by default. These controls are deterministic, so the scores are reproducible.

```bash
arena run benchmark_sets/audit_v1 --reviewer reference-patch     --mode full --allow-local-execution
arena run benchmark_sets/audit_v1 --reviewer control:perfect_patch  --mode full --allow-local-execution
arena run benchmark_sets/audit_v1 --reviewer control:keyword_gamer  --mode full --allow-local-execution
arena run benchmark_sets/audit_v1 --reviewer control:bad_patch      --mode full --allow-local-execution
arena run benchmark_sets/audit_v1 --reviewer control:malformed_patch --mode full --allow-local-execution
```

Rank them, and emit JSON for scripting. These runs used local execution, so they
are trusted-local (unverified); `--include-unverified` shows them on the leaderboard.
Docker alone does not make a run appear without that flag -- default eligibility also
requires full coverage, exact reviewer output, a reviewer that could not reach the answer
key, and a pack digest supplied with `--expected-pack-sha256`:

```bash
arena leaderboard runs/ --metric validated_case_rate --beta 1.0 --include-unverified
arena leaderboard runs/ --metric validated_case_rate --include-unverified --json
```

`reference-patch` and `control:perfect_patch` reach `validated_case_rate=1.000`;
`control:keyword_gamer` shows `detection_f_beta=1.000` with `validated_case_rate=0.000`.

## 4. Build the report snapshot the dashboard reads

```bash
arena audit-report runs/ --output docs/reports/audit-v1-results.md \
  --json-output dashboard/public/reports/audit-v1.json
```

`--json-output` is required to write the dashboard snapshot; it overwrites a tracked,
committed file, so it is never written implicitly. Both outputs are versioned and
validated on write, and runs the harness marked invalid are excluded and reported
rather than aggregated.
Optionally refresh the project-health snapshot:

```bash
python scripts/generate_verification_snapshot.py \
  --run-validation --run-quality-checks --generate-report
```

## 5. Run the API and dashboard

In one terminal:

```bash
arena serve
```

In a second terminal:

```bash
cd dashboard
npm install
npm run dev
```

Open <http://localhost:3000/leaderboard>. The leaderboard ranks the runs you just
generated; `/cases` browses every shipped pack, `/runs/<id>` shows a full trace, and
`/reports/audit-v1` renders the report snapshot. If the API is not running, the home
and leaderboard pages fall back to the committed report snapshot, while `/cases` and
`/runs` explain that they need `arena serve`.

## Docker alternative

```bash
docker compose up --build
```

Serves the API on `:8000` and the dashboard on `:3000`; the dashboard waits for the
API health check before starting.
