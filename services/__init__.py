"""Single service layer (WS3.1).

The CLI (main.py), the API (routes/*) and the Streamlit dashboard call these modules; none of them reaches
into engine, patterns, pairs_trading, ml_patterns, backtester or validation directly (tests/test_import_graph.py).

Every service that needs market data takes a ``providers.DataProvider`` so tests can inject fixtures.
Numbers leave a service as fractions; a non-finite value becomes ``None`` before pydantic validation.
"""
