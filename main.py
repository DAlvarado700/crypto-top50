#!/usr/bin/env python
"""CLI entrypoint for the Crypto Top 50 Quant Tracker."""

from __future__ import annotations

import argparse
import logging
import sys

from src import analytics, btc_indicators, db, export, returns, tracker
from src.coingecko import CoinGeckoClient, CoinGeckoError
from src.config import DB_PATH, load_extra_seed_coin_ids

logger = logging.getLogger("crypto_top50")

BAR_WIDTH = 20
BOX_WIDTH = 52


def setup_logging(verbose: bool) -> None:
    if sys.platform == "win32":
        # Windows consoles default to a legacy codepage that can't render box-drawing
        # characters used by `status`; force UTF-8 on stdout/stderr.
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_init(args: argparse.Namespace) -> None:
    db.init_db()
    print(f"Database initialized at {DB_PATH}")


def cmd_backfill(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    client = CoinGeckoClient()
    try:
        extra_ids = load_extra_seed_coin_ids()
        result = tracker.run_backfill(conn, client, years=args.years, extra_coin_ids=extra_ids)
        print(f"Backfill done: {result.get('days_processed', 0)} historical days replayed "
              f"across {result.get('universe_size', 0)} coins.")

        # Backfill deliberately stops at yesterday (see run_backfill docstring/comment);
        # today's row always comes from the live top-N endpoint, which is a complete,
        # consistent read rather than a possibly-partial historical data point.
        live = tracker.run_live_snapshot(conn, client)
        print(f"Live snapshot for today recorded: {len(live['entries'])} entries, "
              f"{len(live['exits'])} exits, {len(live['continuing'])} continuing.")
    except CoinGeckoError as exc:
        logger.error("Backfill failed: %s", exc)
        sys.exit(1)
    finally:
        conn.close()


def cmd_snapshot(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    client = CoinGeckoClient()
    try:
        result = tracker.run_live_snapshot(conn, client)
        print(f"Snapshot recorded: {len(result['entries'])} entries, "
              f"{len(result['exits'])} exits, {len(result['continuing'])} continuing.")
        if result["entries"]:
            print(f"  Entered: {', '.join(result['entries'])}")
        if result["exits"]:
            print(f"  Exited:  {', '.join(result['exits'])}")
        if result["gap_days"]:
            print(f"  Warning: {result['gap_days']}-day gap since last snapshot.")
    except CoinGeckoError as exc:
        logger.error("Snapshot failed: %s", exc)
        sys.exit(1)
    finally:
        conn.close()


def cmd_bitcoin(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    client = CoinGeckoClient()
    try:
        result = btc_indicators.fetch_and_cache(conn, client)
        print(f"BTC price history: {result['btc_price_rows_upserted']} rows upserted "
              f"(Yahoo Finance, since CoinGecko's free tier caps history at 365 days).")
        print(f"Global snapshot recorded: {result['global_snapshot_recorded']}.")
        print(f"Fear & Greed history: {result['fear_greed_rows_upserted']} rows upserted.")
    except CoinGeckoError as exc:
        logger.error("BTC indicators fetch failed: %s", exc)
        sys.exit(1)
    finally:
        conn.close()


def cmd_returns(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    client = CoinGeckoClient()
    try:
        result = returns.compute_pending_returns(conn, client)
        print(f"Returns computed: {result['computed']} new, {result['already_done']} already cached, "
              f"{result['skipped_future']} not yet due, {result['skipped_no_price']} missing price data.")
    except CoinGeckoError as exc:
        logger.error("Returns computation failed: %s", exc)
        sys.exit(1)
    finally:
        conn.close()


def cmd_analyze(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    try:
        analysis = analytics.build_analysis(conn)
        json_path = export.export_analysis_json(analysis)
        data_js_path = export.export_dashboard_data(analysis)
        print(f"Analysis written to {json_path}")
        print(f"Dashboard data written to {data_js_path}")
    finally:
        conn.close()


def cmd_export(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    try:
        if args.format == "csv":
            path = export.export_returns_csv(conn)
            print(f"Returns exported to {path}")
        else:
            analysis = analytics.build_analysis(conn)
            path = export.export_analysis_json(analysis)
            print(f"Analysis exported to {path}")
    finally:
        conn.close()


def cmd_run(args: argparse.Namespace) -> None:
    cmd_snapshot(args)
    cmd_returns(args)
    cmd_bitcoin(args)
    cmd_analyze(args)


def _bar(pct: float, width: int = BAR_WIDTH) -> str:
    filled = round((pct / 100) * width)
    return "█" * filled + " " * (width - filled)


def render_status(analysis: dict) -> str:
    meta = analysis["meta"]
    lines = []
    lines.append("┌─ TOP 50 CRYPTO TRACKER " + "─" * (BOX_WIDTH - 24) + "┐")

    if meta["snapshot_days"] == 0:
        lines.append("│ No data yet -- run `python main.py backfill` first".ljust(BOX_WIDTH + 1) + "│")
        lines.append("└" + "─" * BOX_WIDTH + "┘")
        return "\n".join(lines)

    span = f"{meta['first_snapshot_date']} -> {meta['last_snapshot_date']}"
    lines.append(f"│ Snapshots:          {meta['snapshot_days']} days ({span})".ljust(BOX_WIDTH + 1) + "│")
    lines.append(f"│ Coins tracked:      {meta['coins_tracked']}".ljust(BOX_WIDTH + 1) + "│")
    lines.append(
        f"│ Tenures total:      {meta['tenures_total']} "
        f"({meta['tenures_active']} active, {meta['tenures_closed']} closed)".ljust(BOX_WIDTH + 1) + "│"
    )
    lines.append(
        f"│ Returns computed:   {meta['returns_computed']} / {meta['returns_expected']}".ljust(BOX_WIDTH + 1) + "│"
    )

    lines.append("├" + "─" * BOX_WIDTH + "┤")
    lines.append("│ CURRENT COMPOSITION".ljust(BOX_WIDTH + 1) + "│")
    for c in analysis["composition_current"][:6]:
        label = f"{c['category'][:14]:14s}"
        lines.append(f"│   {label} {c['pct']:5.1f}%  {_bar(c['pct'])}".ljust(BOX_WIDTH + 1) + "│")

    lines.append("├" + "─" * BOX_WIDTH + "┤")
    lines.append("│ AVG RETURN d90-ish (by milestone d100) BY CATEGORY".ljust(BOX_WIDTH + 1) + "│")
    ranked = sorted(
        (c for c in analysis["category_stats"] if c["returns"].get("100", {}).get("mean") is not None),
        key=lambda c: c["returns"]["100"]["mean"],
        reverse=True,
    )
    if not ranked:
        lines.append("│   (no d100 returns computed yet)".ljust(BOX_WIDTH + 1) + "│")
    for c in ranked[:6]:
        r = c["returns"]["100"]
        label = f"{c['category'][:14]:14s}"
        lines.append(f"│   {label} {r['mean']:+7.1f}%  (n={r['n']})".ljust(BOX_WIDTH + 1) + "│")

    lines.append("└" + "─" * BOX_WIDTH + "┘")
    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> None:
    conn = db.get_connection()
    try:
        analysis = analytics.build_analysis(conn)
        print(render_status(analysis))
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="Crypto Top 50 Quant Tracker")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create DB + schema").set_defaults(func=cmd_init)

    p = sub.add_parser("backfill", help="reconstruct historical top50")
    p.add_argument(
        "--years", type=int, default=1,
        help="years of history requested (clamped to ~365 days -- CoinGecko free tier limit)",
    )
    p.set_defaults(func=cmd_backfill)

    sub.add_parser("snapshot", help="capture today's top50").set_defaults(func=cmd_snapshot)
    sub.add_parser("returns", help="compute pending returns").set_defaults(func=cmd_returns)
    sub.add_parser("bitcoin", help="fetch/update BTC position indicators (rainbow, regime, dominance, fear & greed)").set_defaults(func=cmd_bitcoin)
    sub.add_parser("analyze", help="generate output/analysis.json").set_defaults(func=cmd_analyze)

    p = sub.add_parser("export", help="export data table")
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    p.set_defaults(func=cmd_export)

    sub.add_parser("run", help="snapshot + returns + analyze").set_defaults(func=cmd_run)
    sub.add_parser("status", help="terminal summary").set_defaults(func=cmd_status)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
