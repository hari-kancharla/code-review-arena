.PHONY: install test lint format typecheck validate lint-cases certify benchmark serve check

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
	arena validate benchmark_sets/realfix_seed_v0

lint-cases:
	arena lint-cases benchmark_sets/v1 --strict
	arena lint-cases benchmark_sets/audit_v1 --strict
	arena lint-cases benchmark_sets/audit_v2 --strict
	arena lint-cases benchmark_sets/realfix_seed_v0 --strict

certify:
	arena certify-pack benchmark_sets/audit_v2 --allow-local-execution --strict certified

benchmark:
	arena run benchmark_sets/v1 --reviewer control:perfect_patch --mode full --allow-local-execution

serve:
	arena serve --host 0.0.0.0 --port 8000

# The full local gate, mirroring CI's backend job. Run before pushing.
check: lint typecheck test validate lint-cases
