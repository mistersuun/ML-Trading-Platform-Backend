.PHONY: sync lint test check nightly metrics-report
sync:
	uv sync --locked
lint:
	uv run ruff check .
test:
	uv run pytest -q -m "not slow and not live"
# Regenerates the synthetic baseline; for real data run scripts/snapshot_bars.py first (owner action, after market close)
metrics-report:
	uv run python scripts/metrics_report.py --synthetic --out docs/baseline/synthetic_metrics.csv
nightly:
	uv run pytest -q -m slow || [ $$? -eq 5 ]
# Same steps as .github/workflows/ci.yml
check: sync lint test
