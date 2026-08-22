# Reviewer Interface

A reviewer implements `BaseReviewer.review(context)` and returns a `ReviewerResponse`.
`CaseContext` provides the pull-request diff, the buggy relevant files, and optional
failed-test or static-analysis output. It omits the `ground_truth` object, so a reviewer
cannot read the seeded location or the gold patch.

A finding may carry `suggested_patch` (a unified diff), `replacement_code`, and
`patch_confidence`. A natural-language `suggested_fix` alone is valid for review mode but
cannot satisfy patch validation in full mode.

## Built-in reviewers

- `control:<mode>`: deterministic controls for testing the harness, not model comparisons.
  Modes: `perfect`, `partial`, `false_positive`, `invalid_json`, `perfect_patch`,
  `bad_patch`, `detects_no_patch`, `false_positive_patch`, `malformed_patch`,
  `keyword_gamer`. `keyword_gamer` emits keyword-rich reviews with superficial patches
  that fail validation; see [metrics.md](metrics.md#adversarial-baseline-keyword_gamer).
- `reference-patch`: loads each case's static `reference.patch`. Use it to validate the
  fixtures and the full pipeline, not to compare models. A missing `reference.patch`
  produces a structured no-patch finding that fails validation cleanly. See
  [reference-patches.md](reference-patches.md).
- `shallow-patch`: a generic adversarial baseline that produces plausible-looking but
  non-repairing patches. Use it to reproduce the detection-versus-validation gap on any
  pack; CI runs it against `audit_v2` and the RealFix seed.
- `custom-command`: benchmarks any external process, which keeps the harness
  model-agnostic.
- `openai:<base_url>` / `http:<url>`: call a local model server directly, with no wrapper
  script. `openai:` speaks the OpenAI chat-completions shape (Ollama, vLLM, LM Studio,
  llama.cpp); `http:` posts the case to a plain JSON endpoint. Pass the model with
  `--model`, or set `ARENA_HTTP_MODEL`; `ARENA_HTTP_API_KEY` is sent as a bearer token
  when the server wants one.

  ```bash
  arena run benchmark_sets/audit_v1 --reviewer openai:http://localhost:11434/v1 \
    --model llama3 --mode full --allow-local-execution
  ```

## Custom command

`custom-command` writes the reviewer-visible case to a JSON file (via
`serialize_reviewer_case`, which excludes ground truth) and runs your command. These
placeholders are expanded per case: `{case_json}`, `{diff_file}`, `{case_id}`,
`{workspace}`. The command prints review JSON to stdout with no Markdown fences; the
output is parsed by `parse_reviewer_output`. See
[custom-command-reviewer.md](custom-command-reviewer.md) for a worked example.

## Output contract: exact, invalid, and development-only salvage

Exact output is the default comparable contract. The HTTP and custom-command reviewers
share one parser and one set of statuses. By default the parser accepts only an exact
response: the raw output must be exactly one strict JSON object that validates as
`ReviewResult` (a finding path may use a Git `a/`/`b/` prefix, resolved against the
reviewer-visible paths). It performs no Markdown-fence stripping, brace extraction,
trailing-comma removal, bare-list wrapping, field insertion, or finding dropping, so all
of those are `invalid`.

Each response records a `parse_status`:

- `exact` -- one clean JSON object. Comparable.
- `invalid` -- failed the contract (bad JSON, wrong shape, unknown field, one bad finding,
  duplicate key, non-finite number, oversized/truncated output). This is a normal reviewer
  failure: it takes the invalid-output penalty and remains comparable. It does not make the
  whole run untrusted.
- `tolerant` / `repaired` -- produced only when you pass `--enable-repair`, a
  development-only mode. Salvage applies documented tolerant transforms (fence/prose/
  trailing-comma) and then deterministic repair (wrap a bare list, default `overall_risk`/
  `review_summary`, drop individually invalid findings), recording every action and the
  input/retained/dropped finding counts. It never calls a model, never relaxes strict JSON
  decoding (duplicate keys and non-finite numbers stay rejected), and never invents a
  findings list. Any tolerant or repaired case makes the run NON-COMPARABLE by default
  (excluded from the default leaderboard, visible with `--include-unverified`).

Write reviewers that emit one exact JSON object. `arena verify-reviewer` prints `VALID`,
`SALVAGED (DEVELOPMENT ONLY, NON-COMPARABLE)`, or `INVALID` with the status, actions,
attempts, and finding counts. The raw output is always retained for audit.
