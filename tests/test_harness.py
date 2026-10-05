"""Meta-tests: the harness itself is offline, deterministic and well-formed."""
import pandas as pd
import pytest
import requests
from pytest_socket import SocketBlockedError

from tests.fixtures import synthetic as S
from tests.fixtures.fake_broker import FakeBroker, install_fake_broker


def test_network_is_blocked():
    with pytest.raises(SocketBlockedError):
        requests.get("http://example.com", timeout=1)


GENERATORS = {
    "gbm": lambda: S.gbm_ohlc(300, 1),
    "rw": lambda: S.random_walk_ohlc(300, 1),
    "ou_a": lambda: S.ou_pair(300, 1)[0],
    "ou_b": lambda: S.ou_pair(300, 1)[1],
    "gap": S.gap_through_stop_frame,
    "split": S.split_day_frame,
    "fx": S.zero_volume_fx_frame,
    "crypto": S.crypto_weekend_frame,
}


@pytest.mark.parametrize("name", list(GENERATORS))
def test_generator_valid_and_deterministic(name):
    a, b = GENERATORS[name](), GENERATORS[name]()
    pd.testing.assert_frame_equal(a, b)
    S.assert_ohlc_valid(a)


def test_seed_changes_output():
    assert not S.gbm_ohlc(100, 1)["Close"].equals(S.gbm_ohlc(100, 2)["Close"])


def test_business_days_vs_calendar_days():
    assert S.gbm_ohlc(100, 1).index.dayofweek.max() <= 4
    assert S.crypto_weekend_frame().index.dayofweek.max() == 6


def test_special_frames():
    gap = S.gap_through_stop_frame()
    assert gap["Open"].iloc[30] == 80.0 and gap["Close"].iloc[29] == 100.0
    split = S.split_day_frame()
    assert split["Close"].iloc[20] / split["Close"].iloc[19] == pytest.approx(0.5)
    assert (S.zero_volume_fx_frame()["Volume"] == 0).all()


def test_ou_pair_spread_is_mean_reverting():
    import numpy as np
    a, b = S.ou_pair(2000, 3, beta=1.5, half_life=10)
    spread = np.log(a["Close"]) - 1.5 * np.log(b["Close"])
    assert spread.diff().std() < spread.std()  # stationary: level var >> not a drifting walk
    assert abs(np.corrcoef(spread.values[1:], spread.values[:-1])[0, 1]) < 0.99


def test_fake_broker_records_calls(monkeypatch):
    import paper_trader
    broker = install_fake_broker(monkeypatch, FakeBroker(equity=5000, cash=5000, buying_power=10000))
    acct = paper_trader.get_account()
    assert acct["equity"] == 5000 and acct["buying_power"] == 10000
    assert paper_trader.get_positions() == []
    res = paper_trader.execute_signal("AAPL", 1, current_price=100.0)
    assert res["symbol"] == "AAPL"
    assert [c[0] for c in broker.calls] == ["get_account", "get_all_positions", "get_account", "get_all_positions", "get_orders", "submit_order"]
    assert broker.calls_to("submit_order")[0][1][0].symbol == "AAPL"


def test_fake_broker_failures(monkeypatch):
    import paper_trader
    broker = install_fake_broker(monkeypatch)
    broker.timeout_on("get_account")
    assert paper_trader.get_account() is None
    broker.clear_failures()
    broker.raise_on("submit_order")
    assert paper_trader.submit_order("AAPL", 1, "buy") is None
    assert len(broker.calls_to("submit_order")) == 1
    with pytest.raises(TimeoutError):
        broker.timeout_on("get_asset")
        broker.get_asset("AAPL")


def test_patch_fetch_routes_to_synthetic(patch_fetch):
    import data_fetcher
    a = data_fetcher.fetch_ohlcv("AAPL")
    b = data_fetcher.fetch_ohlcv("AAPL")
    c = data_fetcher.fetch_ohlcv("MSFT")
    pd.testing.assert_frame_equal(a, b)
    assert not a["Close"].equals(c["Close"])
    patch_fetch.overrides["XYZ"] = S.gap_through_stop_frame()
    assert len(data_fetcher.fetch_ohlcv("XYZ")) == 40
