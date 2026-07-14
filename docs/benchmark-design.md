# Benchmark Design

Version `v1` contains ten single-primary-bug pull requests. Cases are minimal enough to
inspect but preserve realistic application idioms: dependencies, serializers, cache keys,
database scoping, asynchronous mutation and retrieval-grounding checks.

A case is useful when the bug is behaviorally meaningful, precisely localizable in the PR
diff, and supported by either a regression test or a clear ground-truth explanation.

## Certification and mutation coverage

`arena certify-pack` grades each case: the buggy baseline must fail the tests, the
reference fix must pass them, and (where applicable) mutants of the fixed code must be
killed. Mutation testing is the cheat-resistance signal: it confirms the suite catches a
plausible-but-wrong fix, not just the one reference fix.

Mutation testing is Python-AST based and mutates logic operators (comparisons including
membership, boolean connectives, arithmetic, boolean constants). Two kinds of case
therefore carry no mutation evidence and rest on the baseline-fails plus reference-passes
gates instead: non-Python cases (the mutator cannot parse them), and Python cases whose
fix is structural rather than a logic-operator change (for example introducing a composite
key or a dedup set). `certify-pack` reports coverage (`Mutation evidence: N/M executed
cases`) so this is explicit; a `certified` rating on a case without mutation evidence is a
claim about the baseline and reference gates, not about lookalike-fix resistance.

Mutation evidence is case-level test-oracle strength, measured once at certification
time: it grades the case's tests, not any reviewer. Reviewer-generated patches are
never mutation-tested; a submitted repair is judged by patch application, the case's
required tests, and its validators. Where mutants do exist, a low mutant count is thin
evidence (the per-case report prints the count next to the kill rate), and the
`verified` rung only adds determinism re-runs on top of `certified`; neither rung by
itself implies strong mutation evidence.

