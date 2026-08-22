# Scoring

The legacy review-quality baseline deliberately uses explainable lexical matching. Each
case has one primary seeded bug and receives up to 100 points. In full mode this value is
reported as `review_quality_score`, secondary to deterministic validation.

| Dimension | Points | Rule |
|---|---:|---|
| Concept | 35 | Category plus matches from `concepts` and `must_mention` |
| File | 20 | Normalized path exactly equals an expected path |
| Line | 15 | Full overlap `15`, partial `8`, same file without overlap `3` |
| Severity | 10 | Exact `10`, adjacent level `5`, otherwise `0` |
| Fix | 15 | At least two acceptable fix terms `15`, one `8`, vague fix `3` |
| Precision | 5 | Awarded only when the response has no unmatched findings |

After the positive subtotal, each unmatched finding subtracts
`false_positive_penalty` from `case.yaml`; invalid structured output subtracts
`invalid_json_penalty`. Scores are bounded below by zero.

## Structured output handling

Parsing is exact by default, and exactness is the comparable contract: the raw response
must be exactly one strict JSON object validating as `ReviewResult`. There is no
Markdown-fence stripping, no brace extraction, no trailing-comma removal, no bare-list
wrapping and no field insertion -- anything else is scored as invalid output (with the
invalid-output penalty) while its raw response is preserved. Tolerant salvage exists but
runs only when a caller opts in with `enable_repair`; it is development-only, it records
every transformation and dropped finding, and it marks the run non-comparable, so a
salvaged run is excluded from the default leaderboard.

## Deterministic scoring

Patch-capable runs calculate true positives from detection and localization, then require
configured patch application, tests and structural validators to pass. They report
precision, recall, F1, configurable F-beta, patch application rate, test pass rate,
structural pass rate, false positives per case and cost per true positive.

See [metrics.md](metrics.md) and [patch-validation.md](patch-validation.md).
