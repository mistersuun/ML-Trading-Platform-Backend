#!/usr/bin/env python3
"""
Trading Pattern Bot v2 - command line entry point.

Pipeline: fetch -> detect -> backtest -> (Phase 2: validate) -> stress test -> alert -> order intents.
Nothing is order-eligible until a signal carries a validation_status in config.ORDER_ELIGIBLE_STATUSES,
so in Phase 1 every scan is effectively alert-only. There is no live trading mode.

Commands:
    python main.py scan [--mode technical pairs ml] [--symbol AAPL] [--market stocks]
                        [--stress AAPL ema_crossover] [--report] [--paper]
    python main.py risk init | status | halt --reason TEXT | resume --confirm | rebaseline --confirm | reconcile [--accept]
    python main.py check-llm
    python main.py data-status [--symbol AAPL]
    python main.py rebalance --holdings holdings.csv [--contributions 500]
    python main.py backup --dest PATH
    python main.py run-nightly [--mode technical pairs ml] [--no-stress]
    python main.py schedule [--at 17:30] [--tz America/New_York]
    python main.py heartbeat [--max-age-hours 30]

The old flat flags (python main.py --mode pairs, --paper, ...) still work and mean `scan`.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import allocation
import config
import execution
import scheduler
from claude_integration import check_llm
from logging_setup import install_redaction
from risk_manager import RiskManager
from services import rebalance as rebalance_service
from services import session
from services import stress as stress_service
from services.common import pattern_names
from services.data_status import data_status
from services.providers import default_provider
from services.report import generate_report
from services.scan import run_full_scan
from state import db as state_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════

SUBCOMMANDS = ("scan", "risk", "check-llm", "rebalance", "backup", "data-status",
               "schedule", "run-nightly", "heartbeat")


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _db_or_error():
    try:
        return state_db.require_initialized()
    except state_db.StateNotInitialized as e:
        _err(f"{e}. Run `python main.py risk init` first.")
        return None


def cmd_scan(args) -> int:
    if args.stress:
        sym, pat = args.stress
        logger.info(f"Running full stress test: {sym} / {pat}")
        df = default_provider().ohlcv(sym, config.RESEARCH_LOOKBACK_DAYS)
        if df.empty:
            _err(f"No data for {sym}")
            return 1
        if pat not in pattern_names():
            _err(f"Unknown pattern: {pat}")
            return 1
        report = stress_service.stress_report(df, sym, pat)
        print(json.dumps(report, indent=2, default=str))
        return 0

    paper = bool(args.paper) or config.TRADING_MODE == "paper"

    def one_scan():
        res = run_full_scan(markets=args.market, patterns=args.pattern, symbol_filter=args.symbol,
                            modes=args.mode, paper_trade=paper)
        if args.report:
            generate_report(res)
        return res

    one_scan()
    return 0


def cmd_risk(args) -> int:
    act = args.risk_cmd
    if act == "init":
        conn = state_db.connect()
        try:
            version = state_db.migrate(conn)
        finally:
            conn.close()
        print(f"state DB ready at {config.STATE_DB_PATH} (schema v{version})")
        print("ASSUMPTION (D10): the Alpaca PAPER account is dedicated to this sleeve. The sleeve baseline is taken "
              "from the account at the first trading session; deposits, withdrawals or manual trades there distort "
              "it (a jump > EQUITY_JUMP_HALT_PCT halts; fix with `risk rebaseline --confirm`).")
        return 0
    conn = _db_or_error()
    if conn is None:
        return 1
    try:
        rm = RiskManager(conn)
        if act == "status":
            print(json.dumps(rm.status(), indent=2, default=str))
            return 0
        if act == "halt":
            ev = rm.halt(args.reason)
            print(f"halted: {ev.get('reason')}")
            return 0
        if act == "resume":
            if not args.confirm:
                _err("resume requires --confirm (it clears the halt and resets the drawdown peak)")
                return 2
            rm.resume(confirm=True)
            print("halt cleared; the drawdown peak will re-seed on the next equity update")
            return 0
        if act == "rebaseline":
            if not args.confirm:
                _err("rebaseline requires --confirm (it re-seeds the account baseline and the drawdown peak)")
                return 2
            rm.rebaseline(confirm=True)
            print("account baseline cleared; it re-seeds (with an alert) at the next trading session")
            return 0
        if act == "reconcile":
            try:
                broker = session.get_broker()
                res = (execution.accept_reconciliation(broker, conn) if args.accept
                       else execution.reconcile(broker, conn))
            except Exception as e:
                _err(f"could not read the paper account ({type(e).__name__}); nothing accepted")
                return 1
            print("reconciled: OK" if res.ok else "MISMATCH:\n  " + "\n  ".join(res.mismatches))
            if args.accept and res.ok:
                print("accepted the broker's current state as known")
            elif not res.ok:
                print("review the paper account, then run `risk reconcile --accept`")
            return 0 if res.ok else 1
    finally:
        conn.close()
    return 2


def cmd_check_llm(args) -> int:
    ok, msg = check_llm()
    print(("OK: " if ok else "FAILED: ") + msg)
    return 0 if ok else 1


def cmd_data_status(args) -> int:
    rows = data_status(default_provider(), [args.symbol] if args.symbol else None)
    print(json.dumps([r.dump() for r in rows], indent=2))
    return 0 if all(r.status == "ok" for r in rows) else 1


def cmd_run_nightly(args) -> int:
    res = scheduler.run_nightly(modes=tuple(args.mode or scheduler.MODES), run_stress=not args.no_stress)
    print(json.dumps(res, indent=2, default=str))
    return {"ok": 0, "locked": 0}.get(res.get("status"), 1)


def cmd_schedule(args) -> int:
    logger.info(f"Nightly run scheduled daily at {args.at} {args.tz}. Press Ctrl+C to stop.")
    try:
        scheduler.schedule_forever(at=args.at, tz=args.tz)
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_heartbeat(args) -> int:
    ok, msg = scheduler.heartbeat_check(max_age_hours=args.max_age_hours)
    print(("OK: " if ok else "FAILED: ") + msg)
    if not ok:
        session.alert(f"Heartbeat: {msg}", kind="heartbeat",
                      dedup_key=f"heartbeat:{datetime.now(timezone.utc).date().isoformat()}")
        return 1
    return 0


def cmd_backup(args) -> int:
    conn = _db_or_error()
    if conn is None:
        return 1
    try:
        state_db.backup(conn, args.dest)
    finally:
        conn.close()
    print(f"backup written to {args.dest}")
    return 0


def cmd_rebalance(args) -> int:
    try:
        holdings = allocation.load_holdings(args.holdings)
    except (OSError, ValueError) as e:
        _err(f"cannot read holdings: {e}")
        return 2
    prices, monthly, failed = rebalance_service.rebalance_inputs(default_provider())
    if failed:
        _err("could not fetch prices for " + ", ".join(failed)
             + " (network or data-source failure); no proposal made")
        return 1
    try:
        proposal = allocation.propose_rebalance(holdings, prices, monthly,
                                                contributions=args.contributions)
    except ValueError as e:
        _err(str(e))
        return 2
    print(proposal.render())
    print("\nAdvisory only: the platform never trades the core or trend sleeve. Place any trades yourself.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trading Pattern Bot v2")
    sub = parser.add_subparsers(dest="command", required=True)

    sc = sub.add_parser("scan", help="run the scan pipeline (default command)")
    sc.add_argument("--mode", nargs="+", choices=["technical", "pairs", "ml"], help="Scan modes to run")
    sc.add_argument("--symbol", type=str, help="Scan single symbol")
    sc.add_argument("--pattern", type=str, nargs="+", help="Specific patterns")
    sc.add_argument("--market", type=str, nargs="+", choices=["stocks", "crypto", "forex", "futures"])
    sc.add_argument("--stress", nargs=2, metavar=("SYMBOL", "PATTERN"),
                    help="Full stress test on symbol/pattern combo")
    sc.add_argument("--report", action="store_true", help="Generate HTML report")
    sc.add_argument("--paper", action="store_true",
                    help="Route order intents to the paper account (also needs TRADING_MODE=paper)")

    rk = sub.add_parser("risk", help="persisted risk state and reconciliation")
    rs = rk.add_subparsers(dest="risk_cmd", required=True)
    rs.add_parser("init", help="create/migrate the state DB (trading refuses without it)")
    rs.add_parser("status", help="show risk state")
    h = rs.add_parser("halt", help="halt all trading")
    h.add_argument("--reason", required=True)
    r = rs.add_parser("resume", help="clear a halt")
    r.add_argument("--confirm", action="store_true")
    rb_ = rs.add_parser("rebaseline", help="re-seed the account baseline after a deposit/withdrawal")
    rb_.add_argument("--confirm", action="store_true")
    rc = rs.add_parser("reconcile", help="compare the paper account with the state DB")
    rc.add_argument("--accept", action="store_true", help="accept the broker's current state as known")

    sub.add_parser("check-llm", help="verify the Anthropic key and model")

    rb = sub.add_parser("rebalance", help="print an advisory rebalance proposal (never trades)")
    rb.add_argument("--holdings", required=True, help="CSV: symbol,quantity (CASH row = dollars)")
    rb.add_argument("--contributions", type=float, default=0.0)

    ds = sub.add_parser("data-status", help="per-symbol data source, coverage and quality")
    ds.add_argument("--symbol", type=str, help="one symbol (default: the whole watchlist)")

    nn = sub.add_parser("run-nightly", help="one locked nightly run: scan, write result files, record, back up")
    nn.add_argument("--mode", nargs="+", choices=["technical", "pairs", "ml"])
    nn.add_argument("--no-stress", action="store_true", help="skip the stress tests")

    sd = sub.add_parser("schedule", help="run the nightly job every day (foreground)")
    sd.add_argument("--at", default=scheduler.DEFAULT_AT, help="HH:MM wall-clock time (default 17:30)")
    sd.add_argument("--tz", default=scheduler.DEFAULT_TZ, help="IANA zone (default America/New_York)")

    hb = sub.add_parser("heartbeat", help="exit non-zero (and alert) if the nightly run is stale or failed")
    hb.add_argument("--max-age-hours", type=float, default=30)

    bk = sub.add_parser("backup", help="back up the state DB")
    bk.add_argument("--dest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    install_redaction()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["scan"] + argv  # legacy flat flags mean `scan`
    args = build_parser().parse_args(argv)
    if args.command not in ("heartbeat", "backup"):   # the dead-man check and backups must work on a bad config
        problem = session.risk_config_problem()
        if problem:
            _err(problem)
            return 2
    return {"scan": cmd_scan, "risk": cmd_risk, "check-llm": cmd_check_llm,
            "rebalance": cmd_rebalance, "backup": cmd_backup,
            "data-status": cmd_data_status, "run-nightly": cmd_run_nightly,
            "schedule": cmd_schedule, "heartbeat": cmd_heartbeat}[args.command](args)



if __name__ == "__main__":
    sys.exit(main())
