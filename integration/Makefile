.PHONY: check test-contract test-capability
check:
	uv run ruff check . --exclude .cvat-src
	uv run pytest -m "not capability" -q
test-contract:
	uv run pytest -m contract -q
test-capability:
	uv run pytest -m capability -q
