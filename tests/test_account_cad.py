"""Account snapshots, CAD valuation, leverage and the CAD allocation profile (D14). Invented numbers, fake provider."""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import allocation
import config
import instruments
import scheduler
from account import store
from account.model import AccountSnapshot, SnapPosition, yfinance_symbol
from account.sync import snapshot_from_raw, sync_account
from brokers.ibkr_readonly import IBKRUnavailable, RawAccount, RawPosition
from results import store as results_store
from risk_manager import RiskManager
from services import allocation_view as av
from services import ibkr_sync, portfolio
from services.providers import get_provider
from state import db as state_db
from tests.fixtures.fake_broker import init_state
from tests.test_ui_endpoints import FakeProvider, strict

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- helpers
def raw_account(cash=-25_000.0, net=50_000.0, gross=75_000.0):
    pos = [RawPosition("VFV", "SMART", "TSE", "CAD", "STK", 100, 90.0, 120.0, 12_000.0, 3_000.0),
           RawPosition("CGL.C", "TSE", "", "CAD", "STK", 50, 20.0, 24.0, 1_200.0, 200.0),
           RawPosition("ABCD", "VENTURE", "", "CAD", "STK", 10, 2.0, 3.0, 30.0, 10.0),
           RawPosition("MSFT", "SMART", "NASDAQ", "USD", "STK", 20, 300.0, 400.0, 8_000.0, 2_000.0)]
    return RawAccount(account="U0000001", base_currency="CAD",
                      summary={"NetLiquidation": net, "TotalCashValue": cash, "GrossPositionValue": gross,
                               "BuyingPower": 12_000.0, "ExcessLiquidity": 9_000.0, "MaintMarginReq": 21_000.0},
                      cash_by_currency={"CAD": cash}, fx_rates={"CAD": 1.0, "USD": 1.35}, positions=pos)


def snap_with(positions, cash=-25_000.0, net=50_000.0, gross=75_000.0, as_of=None, fx=None):
    return AccountSnapshot(as_of=(as_of or datetime.now(timezone.utc)).isoformat(), base_currency="CAD",
                           net_liquidation=net, cash=cash, gross_positions=gross, excess_liquidity=9_000.0,
                           maint_margin=21_000.0, buying_power=12_000.0, positions=positions,
                           fx_rates=fx or {"CAD": 1.0, "USD": 1.35}, source="ibkr")


def P(sym, qty, ccy="CAD"):
    return SnapPosition(symbol=sym, quantity=qty, currency=ccy, broker_symbol=sym.split(".")[0])


@pytest.fixture
def client():
    from server import app
    def make(provider):
        app.dependency_overrides[get_provider] = lambda: provider
        return TestClient(app, raise_server_exceptions=False)
    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def db(monkeypatch, tmp_path):
    init_state(monkeypatch, tmp_path)
    conn = state_db.connect()
    yield conn
    conn.close()


@pytest.fixture
def no_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HOLDINGS_CSV", str(tmp_path / "none.csv"))


# ---------------------------------------------------------------- symbol mapping
@pytest.mark.parametrize("sym,exch,ccy,prim,want", [
    ("VFV", "SMART", "CAD", "TSE", "VFV.TO"), ("XEF", "TSE", "CAD", "", "XEF.TO"), ("ZEB", "TSX", "CAD", "", "ZEB.TO"),
    ("ABCD", "VENTURE", "CAD", "", "ABCD.V"), ("CGL.C", "TSE", "CAD", "", "CGL-C.TO"),
    ("KILO.B", "SMART", "CAD", "TSE", "KILO-B.TO"),
    ("MSFT", "SMART", "USD", "NASDAQ", "MSFT"), ("SPY", "ARCA", "USD", "", "SPY"), ("IBM", "NYSE", "USD", "", "IBM"),
    ("BRK B", "SMART", "USD", "NYSE", "BRK-B"),
    ("CCO", "SMART", "CAD", "", "CCO.TO"),                 # SMART-routed CAD with no primary exchange: TSX
    ("VTI", "SMART", "USD", "", "VTI"),
])
def test_tsx_symbol_mapping(sym, exch, ccy, prim, want):
    assert yfinance_symbol(sym, exch, ccy, prim) == want


def test_snapshot_from_raw_maps_symbols_and_registers_external_instruments():
    snap = snapshot_from_raw(raw_account(), NOW)
    assert [p.symbol for p in snap.positions] == ["VFV.TO", "CGL-C.TO", "ABCD.V", "MSFT"]
    assert snap.positions[0].broker_symbol == "VFV" and snap.positions[0].exchange == "TSE"
    inst = instruments.get("VFV.TO")
    assert inst.asset_class == "external" and inst.executable is False and inst.alpaca_trade_symbol is None
    assert inst.yfinance_symbol == "VFV.TO"
    with pytest.raises(instruments.UnknownSymbol):
        instruments.get("NOTREGISTERED.TO")
    assert instruments.bars_per_year("VFV.TO") == 252


# ---------------------------------------------------------------- negative cash, leverage, FX
def test_negative_cash_is_a_margin_loan_and_leverage_is_gross_over_net():
    snap = snapshot_from_raw(raw_account(), NOW)
    assert snap.cash == -25_000 and snap.margin_loan == 25_000
    assert snap.net_liquidation == 50_000 and snap.gross_positions == 75_000
    assert snap.leverage == pytest.approx(1.5)
    assert snap.excess_liquidity == 9_000 and snap.maint_margin == 21_000
    assert snap.holdings()["CASH"] == -25_000 and snap.holdings()["VFV.TO"] == 100
    flat = snapshot_from_raw(raw_account(cash=5_000, net=80_000, gross=75_000), NOW)
    assert flat.margin_loan == 0 and flat.leverage == pytest.approx(75_000 / 80_000)
    assert AccountSnapshot(as_of="x", base_currency="CAD", net_liquidation=0, cash=0, gross_positions=1).leverage is None


def test_fx_rates_convert_foreign_positions():
    snap = snapshot_from_raw(raw_account(), NOW)
    assert snap.fx("CAD") == 1.0 and snap.fx("USD") == 1.35 and snap.fx("EUR") is None
    assert 20 * 400.0 * snap.fx("USD") == pytest.approx(10_800)


# ---------------------------------------------------------------- persistence
def test_snapshot_persisted_history_kept_and_latest_returned(db):
    assert store.latest_snapshot(db) is None
    a = snapshot_from_raw(raw_account(), NOW - timedelta(days=1))
    b = snapshot_from_raw(raw_account(cash=-10_000, net=65_000, gross=75_000), NOW)
    ia, ib = store.save_snapshot(db, a), store.save_snapshot(db, b)
    assert ib > ia
    latest = store.latest_snapshot(db)
    assert latest.id == ib and latest.cash == -10_000 and latest.net_liquidation == 65_000
    assert [p.symbol for p in latest.positions] == ["VFV.TO", "CGL-C.TO", "ABCD.V", "MSFT"]
    assert latest.fx_rates == {"CAD": 1.0, "USD": 1.35} and latest.leverage == pytest.approx(75 / 65)
    assert latest.positions[3].currency == "USD" and latest.positions[0].avg_cost == 90.0
    assert [s.id for s in store.history(db)] == [ib, ia]            # both rows kept, newest first
    assert db.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0] == 2


def test_latest_snapshot_is_none_without_a_state_db():
    assert store.latest_snapshot() is None              # the autouse fixture points STATE_DB_PATH at a missing file


def test_sync_account_stores_a_snapshot_and_unavailable_stores_nothing(db):
    snap = sync_account(db, fetch=raw_account, now=NOW)
    assert snap.id and store.latest_snapshot(db).net_liquidation == 50_000

    def down():
        raise IBKRUnavailable("gateway down")
    with pytest.raises(IBKRUnavailable):
        sync_account(db, fetch=down)
    assert db.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0] == 1


# ---------------------------------------------------------------- holdings.csv fallback
def test_load_holdings_allows_negative_cash_and_a_currency_column(tmp_path):
    f = tmp_path / "h.csv"
    f.write_text("symbol,quantity,currency\nVFV.TO,10,CAD\nMSFT,5,USD\nXEF.TO,3,\nCASH,-2500.5,\n")
    h = allocation.load_holdings(f)
    assert h == {"VFV.TO": 10, "MSFT": 5, "XEF.TO": 3, "CASH": -2500.5}
    h2, ccy = allocation.load_holdings_ex(f)
    assert ccy == {"VFV.TO": "CAD", "MSFT": "USD", "XEF.TO": "CAD"}          # blank -> BASE_CURRENCY
    assert allocation.load_holdings_ex(f, default_currency="USD")[1]["XEF.TO"] == "USD"
    bad = tmp_path / "bad.csv"
    bad.write_text("symbol,quantity\nVFV.TO,-1\n")
    with pytest.raises(ValueError, match=">= 0"):
        allocation.load_holdings(bad)
    bad.write_text("symbol,quantity,currency\nCASH,5,USD\n")
    with pytest.raises(ValueError, match="base currency"):
        allocation.load_holdings(bad)
    bad.write_text("symbol,quantity\nCASH,nan\n")
    with pytest.raises(ValueError):
        allocation.load_holdings(bad)


def test_holdings_csv_fallback_has_the_snapshot_shape(tmp_path):
    f = tmp_path / "h.csv"
    f.write_text("symbol,quantity,currency\nVFV.TO,100,CAD\nMSFT,20,USD\nCASH,-5000,\n")
    snap = store.snapshot_from_holdings(f, prices={"VFV.TO": 100.0, "MSFT": 400.0}, fx_rates={"USD": 1.35}, now=NOW)
    assert isinstance(snap, AccountSnapshot) and snap.source == "holdings_csv" and snap.base_currency == "CAD"
    assert snap.cash == -5000 and snap.margin_loan == 5000
    assert snap.gross_positions == pytest.approx(100 * 100 + 20 * 400 * 1.35)
    assert snap.net_liquidation == pytest.approx(snap.gross_positions - 5000)
    assert snap.leverage == pytest.approx(snap.gross_positions / snap.net_liquidation) and snap.leverage > 1
    assert {p.symbol: p.currency for p in snap.positions} == {"VFV.TO": "CAD", "MSFT": "USD"}
    bare = store.snapshot_from_holdings(f)                       # no prices: quantities only
    assert bare.positions[0].market_value is None and bare.cash == -5000


# ---------------------------------------------------------------- overview
def _csv(tmp_path, monkeypatch, rows):
    p = tmp_path / "holdings.csv"
    p.write_text("symbol,quantity,currency\n" + "\n".join(rows) + "\n")
    monkeypatch.setattr(config, "HOLDINGS_CSV", str(p))


def _expected_net_series(prov, qty_cad, qty_usd, cash):
    """Net worth series: constant holdings priced in CAD (USD ones x USDCAD history) plus constant cash."""
    fx = av.clean_close(prov.frame("USDCAD=X"))
    tot = None
    for s, q in qty_cad.items():
        c = av.clean_close(prov.frame(s)) * q
        tot = c if tot is None else tot + c
    for s, q in qty_usd.items():
        c = av.clean_close(prov.frame(s)) * q
        tot = tot + c * fx
    return tot + cash


def test_overview_from_snapshot_has_net_worth_loan_leverage_headroom_and_fx(client, db, no_csv):
    positions = [P("VFV.TO", 100), P("XEF.TO", 80), P("HBB.TO", 40), P("MSFT", 20, "USD")]
    store.save_snapshot(db, snap_with(positions))
    prov = FakeProvider()
    r = client(prov).get("/api/portfolio/overview?range=1Y")
    assert r.status_code == 200, r.text
    b = strict(r)
    last = {s: float(av.clean_close(prov.frame(s)).iloc[-1]) for s in ("VFV.TO", "XEF.TO", "HBB.TO", "MSFT")}
    pos_value = (100 * last["VFV.TO"] + 80 * last["XEF.TO"] + 40 * last["HBB.TO"] + 20 * last["MSFT"] * 1.35)
    assert b["source"] == "ibkr" and b["base_currency"] == "CAD"
    assert b["net_worth"] == 50_000 and b["total_value"] == 50_000        # the broker's net liquidation
    assert b["positions_value"] == pytest.approx(pos_value)                # bar store, USD converted at 1.35
    assert b["margin_loan"] == 25_000 and b["cash"] == -25_000
    assert b["leverage"] == pytest.approx(1.5) and b["margin_headroom"] == 9_000
    acc = b["account"]
    assert acc["available"] and acc["source"] == "ibkr" and acc["net_worth"] == 50_000
    assert acc["margin_loan"] == 25_000 and acc["leverage"] == pytest.approx(1.5)
    assert acc["margin_headroom"] == 9_000 and acc["maint_margin"] == 21_000
    assert acc["positions_value"] == pytest.approx(pos_value)
    # leverage above MAX_LEVERAGE_WARN (1.0) -> a warning string, in the block and in the warnings list
    warn = [w for w in b["warnings"] if w.startswith("Leverage 1.50x")]
    assert warn and "margin loan 25,000 CAD" in warn[0] and acc["warnings"] == warn
    # growth of 100 / drawdown are on NET worth: constant holdings + constant loan, stated in the method
    m = b["method"].lower()
    assert "net worth" in m and "constant" in m and "margin loan" in m and "usdcad=x" in m
    s = b["series"]
    exp = _expected_net_series(prov, {"VFV.TO": 100, "XEF.TO": 80, "HBB.TO": 40}, {"MSFT": 20}, -25_000)
    base_pos = portfolio._range_base(exp.index, "1Y")
    exp = exp.iloc[base_pos:]
    assert s[0]["portfolio"] == pytest.approx(100.0)
    # the last bar uses the broker's FX rate rather than the history, so compare through the first and middle
    mid = len(s) // 2
    assert s[mid]["date"] == exp.index[mid].date().isoformat()
    assert s[mid]["portfolio"] == pytest.approx(exp.iloc[mid] / exp.iloc[0] * 100, rel=1e-9)
    assert all(p["drawdown"] <= 0 for p in s)
    # net-worth growth is more volatile than gross-positions growth because the loan is constant
    gross_only = exp + 25_000
    assert (exp.iloc[-1] / exp.iloc[0]) != pytest.approx(gross_only.iloc[-1] / gross_only.iloc[0])


def test_overview_no_warning_without_margin(client, db, no_csv):
    store.save_snapshot(db, snap_with([P("VFV.TO", 100)], cash=5_000, net=50_000, gross=45_000))
    b = strict(client(FakeProvider()).get("/api/portfolio/overview"))
    assert b["margin_loan"] == 0 and b["leverage"] == pytest.approx(0.9)
    assert not [w for w in b["warnings"] if w.startswith("Leverage")] and b["account"]["warnings"] == []


def test_overview_stale_snapshot_warns(client, db, no_csv):
    store.save_snapshot(db, snap_with([P("VFV.TO", 100)], as_of=datetime.now(timezone.utc) - timedelta(days=5)))
    b = strict(client(FakeProvider()).get("/api/portfolio/overview"))
    assert any("days old" in w for w in b["account"]["warnings"])


def test_overview_csv_fallback_with_negative_cash_has_the_account_block(client, tmp_path, monkeypatch):
    _csv(tmp_path, monkeypatch, ["VFV.TO,100,CAD", "XEF.TO,80,CAD", "CASH,-4000,"])
    prov = FakeProvider()
    b = strict(client(prov).get("/api/portfolio/overview"))
    last = {s: float(av.clean_close(prov.frame(s)).iloc[-1]) for s in ("VFV.TO", "XEF.TO")}
    inv = 100 * last["VFV.TO"] + 80 * last["XEF.TO"]
    assert b["source"] == "holdings_csv" and b["cash"] == -4000 and b["margin_loan"] == 4000
    assert b["positions_value"] == pytest.approx(inv) and b["net_worth"] == pytest.approx(inv - 4000)
    assert b["total_value"] == pytest.approx(inv - 4000)
    assert b["leverage"] == pytest.approx(inv / (inv - 4000)) and b["margin_headroom"] is None
    assert b["account"]["source"] == "holdings_csv"
    assert any(w.startswith("Leverage") for w in b["warnings"])


def test_overview_csv_usd_positions_use_the_fx_history(client, tmp_path, monkeypatch):
    _csv(tmp_path, monkeypatch, ["VFV.TO,100,CAD", "MSFT,10,USD"])
    prov = FakeProvider()
    b = strict(client(prov).get("/api/portfolio/overview"))
    fx = float(av.clean_close(prov.frame("USDCAD=X")).iloc[-1])
    want = (100 * float(av.clean_close(prov.frame("VFV.TO")).iloc[-1])
            + 10 * float(av.clean_close(prov.frame("MSFT")).iloc[-1]) * fx)
    assert b["positions_value"] == pytest.approx(want) and "USDCAD=X" in b["method"]
    missing = strict(client(FakeProvider(missing={"USDCAD=X"})).get("/api/portfolio/overview"))
    assert "MSFT" in missing["unpriced"] and any("USDCAD=X" in w for w in missing["warnings"])


def test_overview_groups_for_tsx_holdings_use_six_groups(client, tmp_path, monkeypatch):
    rows = ["VFV.TO,10", "XUU.TO,10", "HXQ.TO,10", "XEF.TO,10", "ZEB.TO,10", "ZLB.TO,10", "ZUT.TO,10",
            "HBB.TO,10", "XGD.TO,10", "CHPS.TO,10", "COPP.TO,10", "HURA.TO,10", "CCO.TO,10"]
    _csv(tmp_path, monkeypatch, [r + ",CAD" for r in rows] + ["CASH,-100,"])
    monkeypatch.setattr(config, "ALLOCATION_PROFILE", "us")
    b = strict(client(FakeProvider()).get("/api/portfolio/overview"))
    g = {x["key"]: x for x in b["allocation"]}
    assert len(g) <= 6 and "other" in g                       # 9 candidate groups fold into six rows
    assert sum(x["now"] for x in b["allocation"]) == pytest.approx(1.0)
    assert sum(x["target"] for x in b["allocation"]) == pytest.approx(1.0)
    monkeypatch.setattr(config, "ALLOCATION_PROFILE", "cad")
    rows2 = [r for r in rows if not r.startswith(("CHPS", "COPP", "HURA", "CCO"))]
    _csv(tmp_path, monkeypatch, [r + ",CAD" for r in rows2])
    b = strict(client(FakeProvider()).get("/api/portfolio/overview"))
    keys = [x["key"] for x in b["allocation"]]
    assert keys == ["us_equity", "canadian_equity", "intl_equity", "bonds", "tips_bills", "gold_miners"]
    assert sum(x["now"] for x in b["allocation"]) == pytest.approx(1.0)
    assert sum(x["target"] for x in b["allocation"]) == pytest.approx(1.0)


# ---------------------------------------------------------------- groups
@pytest.mark.parametrize("sym,group,detail", [
    ("VFV.TO", "us_equity", "US equity"), ("XUU.TO", "us_equity", "US equity"), ("ZSP.TO", "us_equity", "US equity"),
    ("VUN.TO", "us_equity", "US equity"), ("HXQ.TO", "us_equity", "US equity (Nasdaq)"),
    ("QQC.TO", "us_equity", "US equity (Nasdaq)"),
    ("XEF.TO", "intl_equity", "International equity"), ("VIU.TO", "intl_equity", "International equity"),
    ("ZEA.TO", "intl_equity", "International equity"), ("XEC.TO", "intl_equity", "International equity (EM)"),
    ("VEE.TO", "intl_equity", "International equity (EM)"),
    ("XIC.TO", "canadian_equity", "Canadian equity"), ("VCN.TO", "canadian_equity", "Canadian equity"),
    ("ZCN.TO", "canadian_equity", "Canadian equity"), ("ZLB.TO", "canadian_equity", "Canadian equity"),
    ("ZEB.TO", "canadian_equity", "Canadian equity"), ("ZUT.TO", "canadian_equity", "Canadian equity"),
    ("HBB.TO", "bonds", "Bonds"), ("XBB.TO", "bonds", "Bonds"), ("ZAG.TO", "bonds", "Bonds"),
    ("VAB.TO", "bonds", "Bonds"),
    ("XGD.TO", "gold_miners", "Gold & miners"), ("CGL.TO", "gold_miners", "Gold & miners"),
    ("CHPS.TO", "thematic", "Thematic & single names"), ("COPP.TO", "thematic", "Thematic & single names"),
    ("HURA.TO", "thematic", "Thematic & single names"), ("CCO.TO", "thematic", "Thematic & single names"),
    ("VTI", "us_equity", "US equity"),
])
def test_group_classification(sym, group, detail):
    assert av.classify(sym) == (group, detail if "." in sym else av.GROUP_LABEL[group])
    assert av.GROUP_LABEL[group]


def test_unknown_symbols_are_in_no_group():
    assert av.classify("ZZZZ.TO") is None and av.group_key("NOPE") is None


def test_groups_beyond_six_fold_into_other():
    target = {k: 0.0 for k, _ in av.GROUPS}
    now = {k: 0.0 for k, _ in av.GROUPS}
    for k, v in (("us_equity", .3), ("canadian_equity", .2), ("intl_equity", .15), ("bonds", .1), ("treasuries", .08),
                 ("tips_bills", .07), ("gold_miners", .06), ("thematic", .04)):
        now[k] = v
    rows = av.group_rows(target, now, profile="cad")
    assert len(rows) == 6 and rows[-1].key == "other"
    assert rows[-1].now == pytest.approx(0.07 + 0.06 + 0.04)         # the three smallest groups
    assert sum(r.now for r in rows) == pytest.approx(1.0)
    assert {r.key for r in rows} >= {"us_equity", "canadian_equity", "intl_equity", "bonds"}


# ---------------------------------------------------------------- risk status
def test_risk_status_has_the_account_block_and_leverage_warning(client, db):
    RiskManager().update_equity(10_000)
    store.save_snapshot(db, snap_with([P("VFV.TO", 100)]))
    r = client(FakeProvider()).get("/api/risk/status")
    assert r.status_code == 200, r.text
    a = strict(r)["account"]
    assert a["available"] and a["source"] == "ibkr" and a["base_currency"] == "CAD"
    assert a["net_worth"] == 50_000 and a["margin_loan"] == 25_000 and a["leverage"] == pytest.approx(1.5)
    assert a["margin_headroom"] == 9_000 and a["max_leverage_warn"] == 1.0
    assert any(w.startswith("Leverage 1.50x") for w in a["warnings"])


def test_risk_status_account_block_without_a_snapshot(client, db):
    RiskManager().update_equity(10_000)
    a = strict(client(FakeProvider()).get("/api/risk/status"))["account"]
    assert a["available"] is False and "ibkr sync" in a["note"] and a["net_worth"] is None


def test_max_leverage_warn_is_configurable(client, db, monkeypatch):
    RiskManager().update_equity(10_000)
    store.save_snapshot(db, snap_with([P("VFV.TO", 100)]))
    monkeypatch.setattr(config, "MAX_LEVERAGE_WARN", 2.0)
    assert strict(client(FakeProvider()).get("/api/risk/status"))["account"]["warnings"] == []


# ---------------------------------------------------------------- allocation: CAD profile, never margin
def test_cad_profile_is_approved_and_the_default():
    assert config.ALLOCATION_PROFILE == "cad" and allocation.active_profile().name == "cad"
    assert allocation.active_profile("us").name == "us"
    cad = allocation.PROFILES["cad"]
    assert not cad.proposed and sum(cad.core.values()) == pytest.approx(1.0)
    assert cad.core["XIC.TO"] == 0.10 and cad.core["VFV.TO"] == pytest.approx(allocation.CORE_TARGETS["VTI"] - 0.10 + 0.05)
    assert sum(allocation.target_weights({}, cad).values()) == pytest.approx(1.0)
    assert set(allocation.target_weights({}, cad)) == set(cad.core) | set(cad.trend_universe)
    text = (ROOT / "docs" / "decisions.md").read_text()
    d14 = text[text.index("## D14"):]
    assert "Approved by owner 2026-10-05" in d14.splitlines()[0] and "PROPOSED" not in d14
    assert "Approved by owner 2026-10-05" in d14 and "tzdata" in d14 and "commodities" in d14.lower()
    assert "margin-loan rule" in d14 and "contribution_to_loan" in d14
    for sym in ("VFV", "XIC", "XEF", "XEC", "ZAG", "ZFL", "ZRR", "CASH.TO", "CGL.C", "XUU", "KILO.B", "ZMMK", "XRB"):
        assert sym in d14
    assert "10 points from US equity" in d14 and "Canadian" in d14


def test_cad_alternates_are_used_when_only_the_alternate_is_held():
    cad = allocation.PROFILES["cad"]
    r = allocation.resolve_profile({"XUU.TO": 10, "XBB.TO": 5}, cad)
    assert "XUU.TO" in r.core and "VFV.TO" not in r.core and "XBB.TO" in r.core and "ZAG.TO" not in r.core
    assert r.core["XUU.TO"] == cad.core["VFV.TO"] and "XUU.TO" in r.trend_universe
    both = allocation.resolve_profile({"XUU.TO": 10, "VFV.TO": 5}, cad)
    assert "VFV.TO" in both.core                                      # primary held: no swap
    assert allocation.resolve_profile({}, cad) is cad


def _cad_prices(extra=()):
    prof = allocation.PROFILES["cad"]
    return {s: 50.0 for s in set(prof.core) | set(prof.trend_universe) | set(extra)}


def _sleeve_target_holdings(prof, prices, total):
    """Whole-share holdings sitting exactly on the profile's target weights (price 50)."""
    w = allocation.target_weights({}, prof)
    return {s: total * x / prices[s] for s, x in w.items() if x}


def _legs(p):
    buys = sum(r.trade_value for r in p.rows if r.trade_shares > 0)
    sells = -sum(r.trade_value for r in p.rows if r.trade_shares < 0)
    return buys, sells


def _notes_are_consistent(p):
    if p.trades:
        assert not any("no trades" in n.lower() or "nothing to rebalance" in n.lower() for n in p.notes), p.notes
    assert "no trades" not in p.render().lower() or not p.trades


def test_a_managed_sleeve_at_target_with_a_loan_and_unmanaged_proposes_nothing():
    prof = allocation.PROFILES["cad"]
    prices = _cad_prices()
    h = _sleeve_target_holdings(prof, prices, 100_000.0) | {"CASH": -40_000.0, "ZEB.TO": 500.0}
    p = allocation.propose_rebalance(h, prices, None, profile=prof, unmanaged_value=30_000.0, account_leverage=1.4,
                                     currency="CAD")
    assert p.trades == [] and p.margin_loan == 40_000 and p.contribution_to_loan == 0
    assert p.managed_value == pytest.approx(100_000)                   # the loan is not charged to the sleeve
    assert all(not r.out_of_band for r in p.rows)
    assert p.total_value == pytest.approx(100_000 + 30_000 - 40_000)
    assert p.leftover_cash == 0
    assert any(n.startswith("Reduce margin loan first") and "CAD 40,000.00" in n for n in p.notes)
    assert "ZEB.TO" in p.unmanaged
    _notes_are_consistent(p)


def test_off_band_sleeve_with_a_loan_is_rebalanced_with_self_funded_trades_only():
    prof = allocation.PROFILES["cad"]
    prices = _cad_prices()
    h = _sleeve_target_holdings(prof, prices, 100_000.0)
    h["VFV.TO"] *= 1.6                                                  # far overweight
    h["XEF.TO"] *= 0.3                                                  # far underweight
    h["CASH"] = -25_000.0
    p = allocation.propose_rebalance(h, prices, None, profile=prof, unmanaged_value=20_000.0, currency="CAD")
    buys, sells = _legs(p)
    assert sells > 0 and buys > 0 and buys <= sells + 1e-6              # own cash is 0: sells fund buys
    assert p.contribution_to_loan == 0
    assert p.leftover_cash == pytest.approx(0 + sells - buys) and p.leftover_cash >= -1e-6
    assert p.margin_loan_after == 25_000                                # untouched: never sold to repay
    assert all(r.trade_shares < 0 for r in p.rows if r.symbol == "VFV.TO" and r.trade_shares)
    _notes_are_consistent(p)
    # with own cash, net cash use never exceeds it
    h2 = dict(h) | {"CASH": 3_000.0}
    p2 = allocation.propose_rebalance(h2, prices, None, profile=prof, currency="CAD")
    b2, s2 = _legs(p2)
    assert b2 - s2 <= 3_000 + 1e-6 and p2.leftover_cash == pytest.approx(3_000 + s2 - b2)
    _notes_are_consistent(p2)


def test_a_contribution_smaller_than_the_loan_goes_entirely_to_the_loan():
    prof = allocation.PROFILES["cad"]
    prices = _cad_prices()
    h = _sleeve_target_holdings(prof, prices, 100_000.0)
    h["XEF.TO"] *= 0.8                                                  # underweight, inside the band
    h["CASH"] = -5_000.0
    p = allocation.propose_rebalance(h, prices, None, contributions=2_000.0, profile=prof, currency="CAD")
    assert p.contribution_to_loan == 2_000 and p.margin_loan_after == 3_000
    assert p.trades == [] and p.leftover_cash == 0
    assert any("Apply CAD 2,000.00 to the margin loan" in n for n in p.notes)
    assert "Apply $2,000.00 to the margin loan" in p.render()
    assert not any("no trades" in n.lower() for n in p.notes)           # the notes describe what happens: no buys
    assert any("Reduce margin loan first" in n for n in p.notes)


def test_a_contribution_larger_than_the_loan_repays_it_and_buys_underweights_with_the_rest():
    prof = allocation.PROFILES["cad"]
    prices = _cad_prices()
    h = _sleeve_target_holdings(prof, prices, 100_000.0)
    h["XEF.TO"] *= 0.5
    h["CASH"] = -1_000.0
    p = allocation.propose_rebalance(h, prices, None, contributions=6_000.0, profile=prof, currency="CAD")
    buys, sells = _legs(p)
    assert p.contribution_to_loan == 1_000 and p.margin_loan_after == 0
    assert sells == 0 and 0 < buys <= 5_000 + 1e-6                      # only the remainder is spendable
    assert any(r.symbol == "XEF.TO" and r.trade_shares > 0 for r in p.rows)
    assert p.leftover_cash == pytest.approx(5_000 - buys)
    assert any("Apply CAD 1,000.00 to the margin loan" in n for n in p.notes)
    _notes_are_consistent(p)


def test_no_managed_holdings_means_nothing_to_rebalance_and_no_trades():
    prof = allocation.PROFILES["cad"]
    for h, contrib in (({"CASH": -57_000.0}, 0.0), ({"CASH": -57_000.0}, 10_000.0), ({}, 0.0)):
        p = allocation.propose_rebalance(h, _cad_prices(), None, contributions=contrib, profile=prof,
                                         unmanaged_value=157_000.0, currency="CAD")
        assert p.trades == [] and all(r.weight == 0 and not r.out_of_band for r in p.rows)
        assert any(n.startswith("Nothing to rebalance") for n in p.notes)
        assert p.render()
    p = allocation.propose_rebalance({"CASH": -57_000.0}, _cad_prices(), None, contributions=10_000.0, profile=prof)
    assert p.contribution_to_loan == 10_000 and p.margin_loan_after == 47_000


def test_notes_never_claim_no_trades_when_trades_exist():
    prof = allocation.PROFILES["cad"]
    prices = _cad_prices()
    for cash, contrib in ((-5_000.0, 0.0), (-5_000.0, 9_000.0), (4_000.0, 0.0), (0.0, 3_000.0)):
        h = _sleeve_target_holdings(prof, prices, 50_000.0)
        h["VFV.TO"] *= 1.7
        h["XIC.TO"] *= 0.2
        h["CASH"] = cash
        p = allocation.propose_rebalance(h, prices, None, contributions=contrib, profile=prof, currency="CAD")
        assert p.trades
        _notes_are_consistent(p)


def test_proposal_without_a_loan_is_unchanged_and_has_no_loan_line():
    prof = allocation.PROFILES["cad"]
    p = allocation.propose_rebalance({"VFV.TO": 10.0, "CASH": 3_000.0}, _cad_prices(), None, profile=prof)
    assert p.margin_loan == 0 and p.leverage is None and not any("margin" in n.lower() for n in p.notes)
    assert sum(r.trade_value for r in p.rows if r.trade_shares > 0) <= 3_000 + 1e-6
    assert not any("PROPOSED" in n for n in p.notes) and p.contribution_to_loan == 0
    assert "margin loan" not in p.render().lower()


def test_proposal_endpoint_cad_profile_with_snapshot_and_loan(client, db, no_csv, monkeypatch):
    monkeypatch.setattr(config, "ALLOCATION_PROFILE", "cad")
    positions = [P("XUU.TO", 400), P("XEF.TO", 100), P("ZAG.TO", 50), P("MSFT", 10, "USD")]
    store.save_snapshot(db, snap_with(positions, cash=-20_000.0, net=30_000.0, gross=50_000.0))
    prov = FakeProvider()
    for contribution in (0, 500):
        b = strict(client(prov).get(f"/api/allocation/proposal?contribution={contribution}"))
        assert b["profile"] == "cad" and b["margin_loan"] == 20_000 and b["leverage"] is not None
        assert any(n.startswith("Reduce margin loan first") for n in b["notes"])
        assert not any("PROPOSED" in n for n in b["notes"])
        syms = {r["symbol"] for r in b["rows"]}
        assert "XUU.TO" in syms and "VFV.TO" not in syms and "XIC.TO" in syms        # alternate held -> used
        buys = sum(t["amount"] for t in b["trades"] if t["action"] == "buy")
        sells = sum(t["amount"] for t in b["trades"] if t["action"] == "sell")
        assert buys <= sells + contribution + 1e-6                                   # self-funded; no own cash
        assert b["cash"] == -20_000 and b["cash_after"] == pytest.approx(sells - buys + 0)
        assert b["contribution_to_loan"] == (contribution or None)
        assert b["margin_loan_after"] == pytest.approx(20_000 - contribution)
        assert not (b["trades"] and any("no trades" in n.lower() for n in b["notes"]))
        assert [g["key"] for g in b["groups"]][:2] == ["us_equity", "canadian_equity"]
        assert len(b["groups"]) <= 6
        assert [t["symbol"] for t in b["trend"]] == list(allocation.resolve_profile(
            {"XUU.TO": 1}, allocation.PROFILES["cad"]).trend_universe)


def test_us_profile_proposal_unchanged_by_default():
    p = allocation.propose_rebalance({"VTI": 10.0, "CASH": 100.0},
                                     {s: 50.0 for s in set(allocation.CORE_TARGETS) | set(allocation.TREND_UNIVERSE)},
                                     None, profile=allocation.PROFILES["us"])
    assert p.profile == "us" and not any("PROPOSED" in n for n in p.notes)


# ---------------------------------------------------------------- scheduler
@pytest.fixture
def nightly_env(tmp_path, monkeypatch):
    init_state(monkeypatch, tmp_path)
    monkeypatch.setattr(results_store, "RESULTS_DIR", tmp_path / "results")
    sent = []
    monkeypatch.setattr(scheduler.session, "alert", lambda msg, kind="signal", dedup_key=None: sent.append(
        (msg, kind, dedup_key)) or True)
    return sent


def _scan_ok(**kw):
    return {"technical": [{"symbol": "AAA"}], "pairs": [], "ml": [], "decisions": []}


def test_scheduler_syncs_before_the_scan_and_stores_the_snapshot(nightly_env, monkeypatch):
    order = []
    monkeypatch.setattr(config, "IBKR_SYNC_ENABLED", True)

    def sync_fn():
        order.append("sync")
        return sync_account(fetch=raw_account, now=NOW)

    def scan_fn(**kw):
        order.append("scan")
        return _scan_ok()
    out = scheduler.run_nightly(modes=("technical",), scan_fn=scan_fn, sync_fn=sync_fn)
    assert out["status"] == "ok" and order == ["sync", "scan"]
    assert out["summary"]["ibkr_sync"] == {"status": "ok", "source": "ibkr"}
    assert store.latest_snapshot().net_liquidation == 50_000 and nightly_env == []


def test_scheduler_falls_back_on_ibkr_unavailable_alerts_and_still_scans(nightly_env):
    def down():
        raise IBKRUnavailable("gateway not logged in")
    out = scheduler.run_nightly(modes=("technical",), scan_fn=_scan_ok, sync_fn=down)
    assert out["status"] == "ok"
    assert results_store.read_latest("technical")[0] == [{"symbol": "AAA"}]       # the scan ran
    assert out["summary"]["ibkr_sync"] == {"status": "fallback", "source": "holdings_csv"}
    assert len(nightly_env) == 1
    msg, kind, key = nightly_env[0]
    assert "IBKR sync failed" in msg and "gateway not logged in" in msg and "holdings.csv" in msg
    assert kind == "account_sync_failed" and key.startswith("ibkr_sync_failed:")


def test_scheduler_fallback_prefers_the_last_snapshot(nightly_env):
    sync_account(fetch=raw_account, now=NOW)

    def down():
        raise IBKRUnavailable("timeout")
    out = scheduler.run_nightly(modes=("technical",), scan_fn=_scan_ok, sync_fn=down)
    assert out["status"] == "ok" and out["summary"]["ibkr_sync"]["source"] == "last_snapshot"
    assert "last snapshot" in nightly_env[0][0]


def test_scheduler_sync_bug_never_blocks_the_scan(nightly_env):
    def broken():
        raise RuntimeError("unexpected")
    out = scheduler.run_nightly(modes=("technical",), scan_fn=_scan_ok, sync_fn=broken)
    assert out["status"] == "ok" and out["summary"]["ibkr_sync"]["status"] == "fallback"


def test_scheduler_skips_the_sync_when_disabled(nightly_env, monkeypatch):
    called = []
    monkeypatch.setattr(ibkr_sync, "sync", lambda *a, **k: called.append(1))
    assert config.IBKR_SYNC_ENABLED is False
    out = scheduler.run_nightly(modes=("technical",), scan_fn=_scan_ok)
    assert out["status"] == "ok" and called == [] and "ibkr_sync" not in out["summary"] and nightly_env == []


# ---------------------------------------------------------------- CLI
def test_cli_ibkr_status_and_sync(db, monkeypatch, capsys):
    import main
    assert main.main(["ibkr", "status"]) == 1                          # no snapshot yet
    assert "no IBKR snapshot" in capsys.readouterr().out
    monkeypatch.setattr(ibkr_sync, "sync", lambda conn=None, fetch=None: sync_account(conn, fetch=raw_account, now=NOW))
    assert main.main(["ibkr", "sync"]) == 0
    out = capsys.readouterr().out
    assert "net liquidation 50,000.00" in out and "leverage 1.50x" in out and "WARNING: Leverage" in out
    assert main.main(["ibkr", "status"]) == 0
    assert "VFV.TO" in capsys.readouterr().out

    def down(conn=None, fetch=None):
        raise IBKRUnavailable("no gateway")
    monkeypatch.setattr(ibkr_sync, "sync", down)
    assert main.main(["ibkr", "sync"]) == 1
    assert "IBKR unavailable" in capsys.readouterr().err


# ---------------------------------------------------------------- review fixes: net value, unmanaged holdings, loan
def _tsx_snapshot(db, cash=-57_000.0, net=100_000.0, gross=157_000.0):
    """Invented account: all TSX listings (unmanaged under the `us` profile), a loan, broker market values in CAD."""
    pos = [SnapPosition(symbol=s, quantity=q, currency="CAD", broker_symbol=s.split(".")[0], sec_type="STK",
                        market_price=v / q, market_value=v)
           for s, q, v in (("VFV.TO", 300, 40_000.0), ("ZEB.TO", 500, 30_000.0), ("CHPS.TO", 400, 20_000.0),
                           ("XGD.TO", 700, 17_000.0))]
    pos.append(SnapPosition(symbol="MSFT", quantity=50, currency="USD", broker_symbol="MSFT", sec_type="STK",
                            market_price=400.0, market_value=20_000.0))
    store.save_snapshot(db, snap_with(pos, cash=cash, net=net, gross=gross))


def test_loan_larger_than_managed_value_gives_rows_and_a_note_not_an_error():
    prof = allocation.PROFILES["cad"]
    p = allocation.propose_rebalance({"VFV.TO": 100.0, "CASH": -9_000.0}, _cad_prices(), None, profile=prof,
                                     unmanaged_value=20_000.0, account_leverage=1.57, currency="CAD")
    assert p.managed_value == pytest.approx(5_000) and p.unmanaged_value == 20_000   # the loan is not in the sleeve
    assert p.total_value == pytest.approx(5_000 - 9_000 + 20_000)               # net = managed + unmanaged + cash
    assert p.leverage == 1.57 and p.margin_loan == 9_000
    assert any("CAD 9,000.00 is borrowed" in n for n in p.notes)                 # the loan carries its currency
    _notes_are_consistent(p)                                                    # trades (if any) never sell to repay


def test_no_managed_holdings_at_all_does_not_raise():
    p = allocation.propose_rebalance({"CASH": -57_000.0}, _cad_prices(), None, profile=allocation.PROFILES["cad"],
                                     unmanaged_value=157_000.0, account_leverage=1.57, currency="CAD")
    assert p.total_value == pytest.approx(100_000) and p.trades == [] and p.leverage == 1.57
    assert p.render()                                                           # renders without error
    flat = allocation.propose_rebalance({}, _cad_prices(), None, profile=allocation.PROFILES["cad"])
    assert flat.trades == [] and flat.total_value == 0


def test_unmanaged_value_is_held_fixed_and_counts_in_net_value():
    prof = allocation.PROFILES["cad"]
    prices = _cad_prices()
    h = {"VFV.TO": 400.0, "XEF.TO": 100.0, "CASH": 1_000.0}                    # managed 25,000 + cash 1,000
    base = allocation.propose_rebalance(h, prices, None, profile=prof)
    withu = allocation.propose_rebalance(h, prices, None, profile=prof, unmanaged_value=74_000.0)
    assert withu.total_value == pytest.approx(100_000) and base.total_value == pytest.approx(26_000)
    assert withu.managed_value == pytest.approx(26_000)
    assert [(r.symbol, r.trade_shares) for r in withu.rows] == [(r.symbol, r.trade_shares) for r in base.rows]
    assert withu.leverage is None                                               # no loan, no account leverage given
    levered = allocation.propose_rebalance({"VFV.TO": 800.0, "CASH": -5_000.0}, prices, None, profile=prof,
                                           unmanaged_value=15_000.0)
    assert levered.leverage == pytest.approx((40_000 + 15_000) / (40_000 - 5_000 + 15_000))   # gross / net
    assert levered.managed_value == pytest.approx(40_000)                       # the loan is not allocated to it


def test_us_profile_with_a_cad_snapshot_gives_200_not_empty_portfolio(client, db, no_csv, monkeypatch):
    monkeypatch.setattr(config, "ALLOCATION_PROFILE", "us")
    _tsx_snapshot(db)
    r = client(FakeProvider()).get("/api/allocation/proposal")
    assert r.status_code == 200, r.text
    b = strict(r)
    assert b["profile"] == "us" and b["base_currency"] == "CAD"
    assert b["trades"] == [] and all(x["current"] == 0 for x in b["rows"])
    assert b["leverage"] == pytest.approx(1.57) and b["margin_loan"] == 57_000   # the broker's gross / net
    assert b["unmanaged_value"] == pytest.approx(40_000 + 30_000 + 20_000 + 17_000 + 20_000 * 1.35)
    assert b["total_value"] == pytest.approx(b["managed_value"] + b["unmanaged_value"] - 57_000)
    assert b["margin_loan_after"] == 57_000 and b["contribution_to_loan"] is None
    assert any("Nothing to rebalance" in n for n in b["notes"])
    assert any("CAD 57,000.00 is borrowed" in n for n in b["notes"])
    assert sorted(b["unmanaged"]) == ["CHPS.TO", "MSFT", "VFV.TO", "XGD.TO", "ZEB.TO"]


def test_cad_profile_total_and_leverage_agree_with_the_broker(client, db, no_csv, monkeypatch):
    monkeypatch.setattr(config, "ALLOCATION_PROFILE", "cad")
    _tsx_snapshot(db)
    b = strict(client(FakeProvider()).get("/api/allocation/proposal"))
    assert b["leverage"] == pytest.approx(1.57)                 # broker gross / net, not managed / total
    unmanaged = 30_000 + 20_000 + 17_000 + 20_000 * 1.35        # ZEB, CHPS, XGD + MSFT at the snapshot's USDCAD
    assert b["unmanaged_value"] == pytest.approx(unmanaged)
    assert b["managed_value"] == pytest.approx(sum(r["value"] for r in b["rows"]))        # loan not allocated
    assert b["total_value"] == pytest.approx(b["managed_value"] + b["unmanaged_value"] - 57_000)
    assert b["cash"] == -57_000


def test_proposal_prices_are_in_base_currency_and_a_us_ticker_on_the_csv_path_is_usd(client, db, tmp_path,
                                                                                    monkeypatch):
    f = tmp_path / "h.csv"
    f.write_text("symbol,quantity\nVTI,10\nVFV.TO,10\nCASH,100\n")             # no currency column, base CAD
    monkeypatch.setattr(config, "ALLOCATION_PROFILE", "us")
    monkeypatch.setattr(config, "HOLDINGS_CSV", str(f))
    assert config.BASE_CURRENCY == "CAD"
    h, ccy = allocation.load_holdings_ex(f, default_currency="CAD", infer=True)
    assert ccy == {"VTI": "USD", "VFV.TO": "CAD"}
    assert allocation.load_holdings_ex(f, default_currency="CAD")[1] == {"VTI": "CAD", "VFV.TO": "CAD"}  # documented
    inp = av.owner_inputs()
    assert av.native_currency("VTI", inp) == "USD" and av.native_currency("VEA", inp) == "USD"
    assert av.native_currency("XEF.TO", inp) == "CAD"
    prov = FakeProvider()
    b = strict(client(prov).get("/api/allocation/proposal"))
    assert b["base_currency"] == "CAD"
    usdcad = float(av.clean_close(prov.frame("USDCAD=X")).iloc[-1])
    vea = next(r for r in b["rows"] if r["symbol"] == "VEA")
    assert vea["price"] == pytest.approx(float(av.clean_close(prov.frame("VEA")).iloc[-1]) * usdcad)


def test_overview_group_values_reconcile_with_positions_and_unclassified(client, db, no_csv):
    positions = [P("VFV.TO", 100), P("XEF.TO", 80), P("MSFT", 20, "USD")]        # MSFT is in no group
    store.save_snapshot(db, snap_with(positions))
    prov = FakeProvider()
    b = strict(client(prov).get("/api/portfolio/overview?range=1Y"))
    last = {s: float(av.clean_close(prov.frame(s)).iloc[-1]) for s in ("VFV.TO", "XEF.TO", "MSFT")}
    want = {"us_equity": 100 * last["VFV.TO"], "intl_equity": 80 * last["XEF.TO"]}
    got = {g["key"]: g["value"] for g in b["allocation"] if g["value"]}
    for k, v in want.items():
        assert got[k] == pytest.approx(v)
    msft = 20 * last["MSFT"] * 1.35
    assert b["unclassified_value"] == pytest.approx(msft)
    assert sum(got.values()) + b["unclassified_value"] == pytest.approx(b["positions_value"])
    assert b["other_value"] == pytest.approx(b["net_worth"] - (b["positions_value"] + b["cash"]))


def test_options_and_futures_are_kept_but_never_valued_as_shares():
    raw = raw_account()
    raw.positions.append(RawPosition("AAPL", "SMART", "", "USD", "OPT", 3, 5.0, 6.0, 1_800.0, 300.0))
    raw.positions.append(RawPosition("ES", "CME", "", "USD", "FUT", 1, 1.0, 1.0, 1.0, 0.0))
    snap = snapshot_from_raw(raw, NOW)
    assert [p.sec_type for p in snap.positions].count("OPT") == 1                 # stored
    assert [p.broker_symbol for p in snap.unvalued_positions()] == ["AAPL", "ES"]
    assert "AAPL" not in snap.holdings() and "ES" not in snap.holdings() and snap.holdings()["MSFT"] == 20
    assert "AAPL" not in snap.currencies()
    with pytest.raises(instruments.UnknownSymbol):
        instruments.get("ES")                                                   # never registered as a stock


def test_overview_warns_about_unvalued_derivatives(client, db, no_csv):
    pos = [P("VFV.TO", 100), SnapPosition(symbol="AAPL", quantity=3, currency="USD", broker_symbol="AAPL",
                                          sec_type="OPT")]
    store.save_snapshot(db, snap_with(pos))
    b = strict(client(FakeProvider()).get("/api/portfolio/overview?range=1Y"))
    assert any("Not valued" in w and "AAPL (OPT)" in w for w in b["warnings"])
    assert all("AAPL" not in x for x in (b["unpriced"],))


def test_sync_failure_alert_kind_is_not_the_trading_halt_kind():
    import alerts
    assert "account_sync_failed" in alerts.ALERT_KINDS and "halt" in alerts.ALERT_KINDS
