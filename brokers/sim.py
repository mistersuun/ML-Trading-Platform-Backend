"""Simulated PAPER broker for the forward test: TradingClient-compatible surface, persisted, fully offline.

Wrap it with ``brokers.alpaca.AlpacaBroker(SimBroker())`` (exactly as tests wrap FakeBroker) and
``execution.submit_intent`` / reconciliation run unchanged. It never touches a network: it imports no HTTP or
broker-SDK library (a test walks this module's import closure to prove it) and prices come from the bar files in
``config.EXTERNAL_BARS_DIR`` (raw IBKR daily JSON, see ``external_bars.py``).

Fill model (daily bars, deliberately conservative):

* An order submitted while the simulation stands at session ``D`` is first eligible on the next session
  ``s > D``. A market order fills at that session's OPEN +/- ``SIM_SLIPPAGE_BPS`` (adverse). A buy limit fills
  when ``low <= limit`` at ``min(open, limit)``.
* A bracket's exit legs go live when the entry fills and are evaluated on the entry bar and every later bar.
  Stop: ``low <= stop`` fills at ``min(open, stop)`` less slippage, so a gap through the stop fills at the open.
  Take profit (a limit sell): ``high >= tp`` fills at ``max(open, tp)`` (no slippage; a gap above fills at the open). If both are touched on one bar the STOP wins.
  When one leg fills the other is cancelled (OCO).
* Commission is 0. Cash starts at ``SIM_START_CASH`` (fictional dollars). No margin: a buy needing more cash than
  is available is rejected at its fill time. Whole shares, long only.
* ``advance_to(date)`` processes every session up to ``date``, fills what is due and marks positions to market.

State lives in one JSON file (``config.SIM_BROKER_PATH``), re-read before and atomically rewritten after every
operation, so it survives restarts and several SimBroker objects over one file stay consistent.
"""
from __future__ import annotations

import json
import os
from datetime import date as _date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pandas as pd

import config
import external_bars

OPEN_STATUSES = ("accepted", "new", "held", "pending_cancel")
TERMINAL = ("filled", "canceled", "rejected", "expired")
_STATE_VERSION = 1


class SimAPIError(Exception):
    """Stand-in for alpaca's APIError (carries .status_code)."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def _enum(v: Any) -> str:
    v = getattr(v, "value", v)
    return str(v).lower().split(".")[-1]


def _f(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def _d(ts: Any) -> str:
    return pd.Timestamp(ts).date().isoformat()


def _r(x: float) -> float:
    return round(float(x), 4)


class SimBroker:
    """TradingClient-like simulated paper account. All public methods mirror alpaca-py's TradingClient."""

    def __init__(self, path: Optional[str] = None, start_cash: Optional[float] = None,
                 slippage_bps: Optional[float] = None, bars: Optional[Callable[[str], pd.DataFrame]] = None):
        self.path = Path(path if path is not None else config.SIM_BROKER_PATH)
        self.start_cash = float(config.SIM_START_CASH if start_cash is None else start_cash)
        self.slippage_bps = float(config.SIM_SLIPPAGE_BPS if slippage_bps is None else slippage_bps)
        self._bars_fn = bars or (lambda sym: external_bars.closed_bars(sym, datetime.now(timezone.utc)))   # finished sessions only
        self._bar_cache: dict = {}
        self.s: dict = self._fresh()
        self._sync()

    # ------------------------------------------------------------------ persistence

    def _fresh(self) -> dict:
        return {"version": _STATE_VERSION, "start_cash": self.start_cash, "cash": self.start_cash, "last_date": None,
                "seq": 0, "positions": {}, "orders": [], "fills": [], "equity_history": []}

    def _sync(self) -> None:
        """Re-read the file (another instance may have written it)."""
        try:
            self.s = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.s = self._fresh()
        if self.s.get("version") != _STATE_VERSION:
            raise SimAPIError(f"unsupported sim state version {self.s.get('version')!r}", 500)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp{os.getpid()}")
        with open(tmp, "w") as fh:
            json.dump(self.s, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ helpers

    def _next_id(self) -> str:
        self.s["seq"] += 1
        return f"sim-{self.s['seq']:06d}"

    def _bars(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._bar_cache:
            self._bar_cache[symbol] = self._bars_fn(symbol)
        return self._bar_cache[symbol]

    def _bar(self, symbol: str, day: str):
        df = self._bars(symbol)
        ts = pd.Timestamp(day)
        return df.loc[ts] if len(df) and ts in df.index else None

    def _find(self, oid: str) -> Optional[dict]:
        for o in self.s["orders"]:
            if o["id"] == oid:
                return o
        return None

    def _slip(self, px: float, side: str) -> float:
        k = self.slippage_bps / 1e4
        return px * (1 + k) if side == "buy" else px * (1 - k)

    def _view(self, o: dict, with_legs: bool = True) -> SimpleNamespace:
        legs = None
        if with_legs and o.get("leg_ids"):
            legs = [self._view(self._find(i), with_legs=False) for i in o["leg_ids"] if self._find(i)]
        return SimpleNamespace(
            id=o["id"], client_order_id=o["client_order_id"], symbol=o["symbol"], qty=str(o["qty"]), side=o["side"],
            type=o["type"], order_type=o["type"], order_class=o["order_class"], status=o["status"], legs=legs,
            limit_price=None if o.get("limit_price") is None else str(o["limit_price"]),
            stop_price=None if o.get("stop_price") is None else str(o["stop_price"]),
            filled_avg_price=None if o.get("filled_avg_price") is None else str(o["filled_avg_price"]),
            filled_qty=str(o["qty"]) if o["status"] == "filled" else "0", time_in_force=o.get("time_in_force", "day"),
            parent_id=o.get("parent_id"), submitted_date=o.get("submitted_date"), filled_date=o.get("filled_date"))

    def _equity(self) -> float:
        return self.s["cash"] + sum(p["qty"] * p["last_price"] for p in self.s["positions"].values())

    @property
    def last_date(self) -> Optional[str]:
        return self.s.get("last_date")

    # ------------------------------------------------------------------ TradingClient surface

    def get_account(self):
        self._sync()
        eq = self._equity()
        return SimpleNamespace(equity=str(_r(eq)), cash=str(_r(self.s["cash"])), buying_power=str(_r(max(self.s["cash"], 0.0))),
                               portfolio_value=str(_r(eq)), daytrade_count=0, status="ACTIVE", currency="USD")

    def get_all_positions(self):
        self._sync()
        out = []
        for sym, p in sorted(self.s["positions"].items()):
            mv = p["qty"] * p["last_price"]
            out.append(SimpleNamespace(
                symbol=sym, qty=str(p["qty"]), side="long", market_value=str(_r(mv)), avg_entry_price=str(_r(p["avg_entry"])),
                current_price=str(_r(p["last_price"])), unrealized_pl=str(_r(mv - p["qty"] * p["avg_entry"])),
                unrealized_plpc="0"))
        return out

    def get_orders(self, *args, **kwargs):
        """Open top-level orders (and filled parents that still have open legs), legs nested."""
        self._sync()
        out = []
        for o in self.s["orders"]:
            if o.get("parent_id"):
                continue
            legs = [self._find(i) for i in o.get("leg_ids", [])]
            if o["status"] in OPEN_STATUSES or any(l and l["status"] in OPEN_STATUSES for l in legs):
                out.append(self._view(o))
        return out

    def get_asset(self, symbol_or_asset_id):
        return SimpleNamespace(symbol=str(symbol_or_asset_id), tradable=True, shortable=False, fractionable=False,
                               status="active")

    def get_clock(self):
        self._sync()
        return SimpleNamespace(is_open=False, timestamp=self.s.get("last_date"), next_open=None, next_close=None)

    def get_order_by_client_id(self, client_order_id: str):
        self._sync()
        for o in self.s["orders"]:
            if o["client_order_id"] == client_order_id and not o.get("parent_id"):
                return self._view(o)
        raise SimAPIError("order not found", 404)

    def submit_order(self, order_data=None, *args, **kwargs):
        self._sync()
        cid = getattr(order_data, "client_order_id", None)
        if cid and any(o["client_order_id"] == cid for o in self.s["orders"]):
            raise SimAPIError("client_order_id must be unique", 422)
        sym = str(order_data.symbol)
        self._require_current_clock(sym)
        side = _enum(order_data.side)
        qty = float(order_data.qty)
        if side not in ("buy", "sell") or qty < 1 or qty != int(qty):
            raise SimAPIError("whole-share buy/sell orders only", 422)
        name = type(order_data).__name__.lower()
        otype = "market" if "market" in name else "stop" if "stop" in name and "limit" not in name else "limit"
        if side == "sell":
            held = self.s["positions"].get(sym, {}).get("qty", 0.0)
            reserved = self._reserved_sell_qty(sym)
            if qty > held - reserved + 1e-9:
                raise SimAPIError(f"insufficient qty available for order (requested {qty:g}, available {held - reserved:g})", 422)
        oclass = _enum(getattr(order_data, "order_class", None) or "simple")
        o = {"id": self._next_id(), "client_order_id": cid or self._next_id(), "symbol": sym, "side": side, "qty": qty,
             "type": otype, "order_class": "bracket" if oclass == "bracket" else "simple", "status": "accepted",
             "limit_price": _f(getattr(order_data, "limit_price", None)), "stop_price": _f(getattr(order_data, "stop_price", None)),
             "time_in_force": _enum(getattr(order_data, "time_in_force", "day")), "submitted_date": self.s["last_date"] or "",
             "parent_id": None, "leg_ids": [], "filled_avg_price": None, "filled_date": None}
        self.s["orders"].append(o)
        if o["order_class"] == "bracket":
            tp = getattr(order_data, "take_profit", None)
            sl = getattr(order_data, "stop_loss", None)
            if side != "buy" or tp is None or sl is None:
                self.s["orders"].remove(o)
                raise SimAPIError("a bracket needs a buy entry with take_profit and stop_loss", 422)
            for kind, px in (("limit", _f(tp.limit_price)), ("stop", _f(sl.stop_price))):
                leg = {"id": self._next_id(), "client_order_id": f"{o['client_order_id']}-{'tp' if kind == 'limit' else 'sl'}",
                       "symbol": sym, "side": "sell", "qty": qty, "type": kind, "order_class": "bracket", "status": "held",
                       "limit_price": px if kind == "limit" else None, "stop_price": px if kind == "stop" else None,
                       "time_in_force": "gtc", "submitted_date": o["submitted_date"], "parent_id": o["id"], "leg_ids": [],
                       "filled_avg_price": None, "filled_date": None}
                self.s["orders"].append(leg)
                o["leg_ids"].append(leg["id"])
        self._save()
        return self._view(o)

    def _require_current_clock(self, sym: str) -> None:
        """An order is only meaningful once the sim clock has caught up with the data the decision was based on.
        Otherwise it would be stamped in the past and could fill on a bar that preceded the decision (look-ahead)."""
        last = self.s["last_date"]
        if last is None:
            raise SimAPIError("sim clock not started (no session processed yet): run `forward step` first", 409)
        df = self._bars(sym)
        if len(df) and df.index[-1].date().isoformat() > last:
            raise SimAPIError(f"sim clock is stale ({last} < newest closed bar {df.index[-1].date().isoformat()} of {sym}): "
                              f"run `forward step` first", 409)

    def cancel_order_by_id(self, order_id):
        self._sync()
        o = self._find(str(order_id))
        if o is None:
            raise SimAPIError("order not found", 404)
        if o["status"] in TERMINAL:
            raise SimAPIError("order is not cancelable", 422)
        o["status"] = "canceled"
        for lid in o.get("leg_ids", []):          # cancelling an unfilled bracket parent cancels its held legs
            leg = self._find(lid)
            if leg and leg["status"] in OPEN_STATUSES and o["filled_date"] is None:
                leg["status"] = "canceled"
        self._save()
        return None

    def close_position(self, symbol_or_asset_id, *args, **kwargs):
        """Cancel the symbol's open orders and queue a market sell of the whole position (fills at the next open)."""
        self._sync()
        sym = str(symbol_or_asset_id)
        pos = self.s["positions"].get(sym)
        if pos is None:
            raise SimAPIError("position does not exist", 404)
        self._require_current_clock(sym)
        for o in self.s["orders"]:
            if o["symbol"] == sym and o["status"] in OPEN_STATUSES:
                o["status"] = "canceled"
        o = {"id": self._next_id(), "client_order_id": f"close-{sym}-{self.s['seq']}", "symbol": sym, "side": "sell",
             "qty": pos["qty"], "type": "market", "order_class": "simple", "status": "accepted", "limit_price": None,
             "stop_price": None, "time_in_force": "day", "submitted_date": self.s["last_date"] or "", "parent_id": None,
             "leg_ids": [], "filled_avg_price": None, "filled_date": None}
        self.s["orders"].append(o)
        self._save()
        return self._view(o)

    def _reserved_sell_qty(self, sym: str) -> float:
        tot = 0.0
        for o in self.s["orders"]:
            if o["symbol"] == sym and o["side"] == "sell" and o["status"] in OPEN_STATUSES and o["parent_id"] is None:
                tot += o["qty"]
        # a bracket's two legs share one reservation (they are OCO)
        groups: dict = {}
        for o in self.s["orders"]:
            if o["symbol"] == sym and o["side"] == "sell" and o["status"] in ("new", "held") and o["parent_id"]:
                groups[o["parent_id"]] = max(groups.get(o["parent_id"], 0.0), o["qty"])
        return tot + sum(groups.values())

    # ------------------------------------------------------------------ simulation

    def advance_to(self, session_date) -> dict:
        """Process every session in (last_date, session_date] for symbols with orders/positions, then mark to
        market. Returns {"sessions": [...], "fills": [new fill dicts], "equity": float}."""
        self._sync()
        target = _d(session_date)
        last = self.s["last_date"]
        new_fills: list = []
        if last is not None and target <= last:
            return {"sessions": [], "fills": [], "equity": _r(self._equity())}
        syms = {o["symbol"] for o in self.s["orders"] if o["status"] in OPEN_STATUSES} | set(self.s["positions"])
        expected = self._expected_sessions(last, target)
        for sym in sorted(syms):                      # never skip a session of a symbol we hold or have orders on
            have = {ts.date().isoformat() for ts in self._bars(sym).index}
            missing = [d for d in expected if d not in have]
            if missing:
                raise SimAPIError(f"{sym} has orders/positions but no bar for session(s) {', '.join(missing[:5])}"
                                  f"{' ...' if len(missing) > 5 else ''}: fetch the missing bars, then retry", 409)
        days: set = set(expected)
        for sym in syms:
            df = self._bars(sym)
            for ts in df.index:
                d = ts.date().isoformat()
                if (last is None or d > last) and d <= target:
                    days.add(d)
        days.add(target)
        done = sorted(days)
        for day in done:
            new_fills += self._process_day(day)
            self._mark(day)
            self.s["last_date"] = day
        self._save()
        return {"sessions": done, "fills": new_fills, "equity": _r(self._equity())}

    @staticmethod
    def _expected_sessions(last: Optional[str], target: str) -> list:
        """XNYS sessions in (last, target]; just the target on a fresh sim (nothing to catch up on)."""
        from data.calendars import get_calendar
        cal = get_calendar("XNYS")
        if last is None:
            return [target] if cal.is_session(target) else []
        return [d.date().isoformat() for d in cal.sessions(pd.Timestamp(last) + pd.Timedelta(days=1), pd.Timestamp(target))]

    def _mark(self, day: str) -> None:
        for sym, p in self.s["positions"].items():
            b = self._bar(sym, day)
            if b is not None:
                p["last_price"] = float(b["Close"])
        eq = self._equity()
        hist = self.s["equity_history"]
        pt = {"date": day, "equity": _r(eq), "cash": _r(self.s["cash"]), "positions": len(self.s["positions"])}
        if hist and hist[-1]["date"] == day:
            hist[-1] = pt
        else:
            hist.append(pt)

    def _process_day(self, day: str) -> list:
        fills: list = []
        # 1) entries / standalone orders submitted before this session
        for o in [x for x in self.s["orders"] if x["status"] in ("accepted", "new") and x["parent_id"] is None]:
            if (o["submitted_date"] or "") >= day:
                continue
            b = self._bar(o["symbol"], day)
            if b is None:
                continue
            px = self._entry_price(o, b)
            if px is None:
                continue
            if o["side"] == "buy" and px * o["qty"] > self.s["cash"] + 1e-6:
                o["status"] = "rejected"
                o["reject_reason"] = "insufficient_cash"
                for lid in o["leg_ids"]:
                    self._find(lid)["status"] = "canceled"
                continue
            if o["side"] == "sell" and o["qty"] > self.s["positions"].get(o["symbol"], {}).get("qty", 0) + 1e-9:
                o["status"] = "rejected"
                o["reject_reason"] = "insufficient_position"
                continue
            fills.append(self._fill(o, px, day))
            for lid in o["leg_ids"]:
                self._find(lid)["status"] = "new"
        # 2) bracket exit legs, evaluated on the entry bar and every later bar: STOP before target on a shared bar
        for parent in [x for x in self.s["orders"] if x["leg_ids"] and x["status"] == "filled"]:
            legs = [self._find(i) for i in parent["leg_ids"]]
            stop = next((l for l in legs if l["type"] == "stop" and l["status"] == "new"), None)
            tp = next((l for l in legs if l["type"] == "limit" and l["status"] == "new"), None)
            if stop is None and tp is None:
                continue
            pos = self.s["positions"].get(parent["symbol"])
            if pos is None:                                   # position gone (closed another way): legs are void
                for l in (stop, tp):
                    if l is not None:
                        l["status"] = "canceled"
                continue
            b = self._bar(parent["symbol"], day)
            if b is None:
                continue
            if stop is not None and float(b["Low"]) <= stop["stop_price"]:
                stop["qty"] = min(stop["qty"], pos["qty"])
                fills.append(self._fill(stop, self._slip(min(float(b["Open"]), stop["stop_price"]), "sell"), day))
                if tp is not None:
                    tp["status"] = "canceled"
            elif tp is not None and float(b["High"]) >= tp["limit_price"]:
                tp["qty"] = min(tp["qty"], pos["qty"])
                fills.append(self._fill(tp, max(float(b["Open"]), tp["limit_price"]), day))   # a gap above the target fills at the open
                if stop is not None:
                    stop["status"] = "canceled"
        return fills

    def _entry_price(self, o: dict, b) -> Optional[float]:
        if o["type"] == "market":
            return self._slip(float(b["Open"]), o["side"])
        if o["type"] == "limit" and o["side"] == "buy":
            return min(float(b["Open"]), o["limit_price"]) if float(b["Low"]) <= o["limit_price"] else None
        if o["type"] == "limit" and o["side"] == "sell":
            return max(float(b["Open"]), o["limit_price"]) if float(b["High"]) >= o["limit_price"] else None
        if o["type"] == "stop" and o["side"] == "sell":
            return self._slip(min(float(b["Open"]), o["stop_price"]), "sell") if float(b["Low"]) <= o["stop_price"] else None
        return None

    def _fill(self, o: dict, px: float, day: str) -> dict:
        px = round(float(px), 4)
        o["status"], o["filled_avg_price"], o["filled_date"] = "filled", px, day
        sym, qty = o["symbol"], float(o["qty"])
        pos = self.s["positions"].get(sym)
        realized = 0.0
        if o["side"] == "buy":
            self.s["cash"] -= px * qty
            if pos is None:
                pos = self.s["positions"][sym] = {"qty": 0.0, "avg_entry": px, "last_price": px}
            pos["avg_entry"] = (pos["avg_entry"] * pos["qty"] + px * qty) / (pos["qty"] + qty)
            pos["qty"] += qty
            pos["last_price"] = px
        else:
            self.s["cash"] += px * qty
            realized = (px - pos["avg_entry"]) * qty
            pos["qty"] -= qty
            pos["last_price"] = px
            if pos["qty"] <= 1e-9:
                del self.s["positions"][sym]
        fill = {"date": day, "id": o["id"], "client_order_id": o["client_order_id"], "symbol": sym, "side": o["side"],
                "qty": qty, "price": px, "type": o["type"], "leg": bool(o["parent_id"]), "realized_pl": _r(realized)}
        self.s["fills"].append(fill)
        return fill
