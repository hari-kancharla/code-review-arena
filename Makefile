.PHONY: install test lint format typecheck validate lint-cases certify benchmark serve check \
	integrity integrity-check

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check arena tests
	ruff format --check arena tests

format:
	ruff format arena tests

typecheck:
	mypy arena

validate:
	arena validate benchmark_sets/v1
	arena validate benchmark_sets/audit_v1
	arena validate benchmark_sets/audit_v2

lint-cases:
	arena lint-cases benchmark_sets/v1 --strict
	arena lint-cases benchmark_sets/audit_v1 --strict
	arena lint-cases benchmark_sets/audit_v2 --strict

certify:
	arena certify-pack benchmark_sets/audit_v2 --allow-local-execution --strict certified

# CRA-Integrity, offline half: structure plus the trivial-heuristic falsifiability
# audit. Fast, no execution, so it belongs in the ordinary gate.
integrity-check:
	arena integrity-validate benchmark_sets/integrity_pilot_v0
	arena integrity-audit benchmark_sets/integrity_pilot_v0

# CRA-Integrity, executing half: every admission gate for every pair, plus the
# control matrix that proves recall alone is not a result.
integrity: integrity-check
	arena integrity-certify benchmark_sets/integrity_pilot_v0 \
		--allow-local-execution --determinism-runs 2 --strict verified
	arena integrity-run benchmark_sets/integrity_pilot_v0 \
		--reviewer integrity:reference --allow-local-execution
	arena integrity-run benchmark_sets/integrity_pilot_v0 \
		--reviewer integrity:blanket-reject --allow-local-execution
	arena integrity-run benchmark_sets/integrity_pilot_v0 \
		--reviewer integrity:no-change --allow-local-execution
	arena integrity-run benchmark_sets/integrity_pilot_v0 \
		--reviewer integrity:product-only-overfit --allow-local-execution

benchmark:
	arena run benchmark_sets/v1 --reviewer control:perfect_patch --mode full --allow-local-execution

serve:
	arena serve --host 0.0.0.0 --port 8000

# The full local gate, mirroring CI's backend job. Run before pushing.
check: lint typecheck test validate lint-cases integrity-check
