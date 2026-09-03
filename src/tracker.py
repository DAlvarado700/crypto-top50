"""Top-50 entry/exit detection. Shared by both live `snapshot` and `backfill` replay."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta

from src import db
from src.coingecko import CoinGeckoClient
from src.config import load_categories

logger = logging.getLogger(__name__)


def rows_from_markets(markets: list[dict], limit: int = 50) -> list[dict]:
    """Converts a CoinGecko /coins/markets response (already sorted desc by market cap)
    into snapshot rows, using our own 1..limit rank rather than the API's market_cap_rank
    (which can be null or briefly inconsistent for freshly listed coins)."""
    rows = []
    for i, m in enumerate(markets[:limit], start=1):
        rows.append(
            {
                "coin_id": m["id"],
                "symbol": m.get("symbol", "").upper(),
                "name": m.get("name", m["id"]),
                "rank": i,
                "price_usd": m.get("current_price"),
                "market_cap": m.get("market_cap"),
                "volume_24h": m.get("total_volume"),
            }
        )
    return rows


def fetch_top50(client: CoinGeckoClient) -> list[dict]:
    markets = client.get_top_markets(50)
    return rows_from_markets(markets, limit=50)


def _get_previous_snapshot_date(conn: sqlite3.Connection, before_date: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM snapshots WHERE snapshot_date < ?",
        (before_date,),
    ).fetchone()
    return row["d"] if row and row["d"] else None


def _days_between(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def apply_snapshot(
    conn: sqlite3.Connection,
    snapshot_date: str,
    rows: list[dict],
    categories: dict[str, str] | None = None,
    fallback: str = "Other",
) -> dict:
    """Records one day's top-50 snapshot and updates tenures accordingly.

    Returns a summary dict: {entries, exits, continuing, uncategorized, gap_days,
    already_recorded}. Idempotent for the same snapshot_date: if this exact date was
    already processed (checked via snapshot_log), row data is refreshed but entry/exit
    transitions are NOT recomputed -- otherwise re-running `snapshot` twice on the same
    day would re-diff against the same previous day and open duplicate tenures.
    """
    if categories is None:
        categories, fallback = load_categories()

    already_recorded = (
        conn.execute("SELECT 1 FROM snapshot_log WHERE snapshot_date = ?", (snapshot_date,)).fetchone()
        is not None
    )

    uncategorized: list[str] = []

    for row in rows:
        category = categories.get(row["coin_id"])
        if category is None:
            category = fallback
            uncategorized.append(f"{row['coin_id']} ({row['symbol']})")
        db.upsert_coin(
            conn,
            coin_id=row["coin_id"],
            symbol=row["symbol"],
            name=row["name"],
            category=category,
            first_seen=snapshot_date,
        )

    if uncategorized:
        logger.warning(
            "%d coin(s) without an explicit category (using fallback '%s'): %s",
            len(uncategorized),
            fallback,
            ", ".join(uncategorized),
        )

    db.insert_snapshot_rows(conn, snapshot_date, rows)

    if already_recorded:
        conn.commit()
        logger.info("Snapshot for %s was already recorded -- row data refreshed, tenures untouched", snapshot_date)
        today_ids = {r["coin_id"] for r in rows}
        return {
            "entries": [],
            "exits": [],
            "continuing": sorted(today_ids),
            "uncategorized": uncategorized,
            "gap_days": 0,
            "already_recorded": True,
        }

    prev_date = _get_previous_snapshot_date(conn, snapshot_date)
    today_ids = {r["coin_id"] for r in rows}
    price_by_id = {r["coin_id"]: r["price_usd"] for r in rows}
    rank_by_id = {r["coin_id"]: r["rank"] for r in rows}

    gap_days = 0
    has_gap = False
    if prev_date is not None:
        gap_days = max(0, _days_between(prev_date, snapshot_date) - 1)
        has_gap = gap_days > 0
        prev_ids = db.get_snapshot_coin_ids(conn, prev_date)
    else:
        prev_ids = set()

    entries = today_ids - prev_ids
    exits = prev_ids - today_ids
    continuing = today_ids & prev_ids

    for coin_id in entries:
        db.open_tenure(
            conn,
            coin_id=coin_id,
            entry_date=snapshot_date,
            entry_price=price_by_id[coin_id],
            entry_rank=rank_by_id[coin_id],
            has_gap=has_gap,
        )

    for coin_id in exits:
        tenure = db.get_open_tenure(conn, coin_id)
        if tenure is None:
            logger.warning("Coin %s exited top50 on %s but had no open tenure -- skipping", coin_id, snapshot_date)
            continue
        exit_price_row = conn.execute(
            "SELECT price_usd FROM snapshots WHERE snapshot_date = ? AND coin_id = ?",
            (prev_date, coin_id),
        ).fetchone()
        exit_price = exit_price_row["price_usd"] if exit_price_row else None
        days = _days_between(tenure["entry_date"], prev_date)
        db.close_tenure(conn, tenure["tenure_id"], exit_date=prev_date, exit_price=exit_price, days_in_top50=days)

    for coin_id in continuing:
        tenure = db.get_open_tenure(conn, coin_id)
        if tenure is None:
            continue
        days = _days_between(tenure["entry_date"], snapshot_date)
        db.update_tenure_days(conn, tenure["tenure_id"], days)
        if has_gap:
            db.mark_tenure_gap(conn, tenure["tenure_id"])

    db.log_snapshot(conn, snapshot_date, gap_days)
    conn.commit()

    return {
        "entries": sorted(entries),
        "exits": sorted(exits),
        "continuing": sorted(continuing),
        "uncategorized": uncategorized,
        "gap_days": gap_days,
        "already_recorded": False,
    }


def run_live_snapshot(conn: sqlite3.Connection, client: CoinGeckoClient) -> dict:
    """Fetches today's real top 50 from the API and applies it."""
    rows = fetch_top50(client)
    categories, fallback = load_categories()
    snapshot_date = datetime.now().date().isoformat()
    return apply_snapshot(conn, snapshot_date, rows, categories, fallback)


def _ms_to_date_str(ts_ms: float) -> str:
    return datetime.utcfromtimestamp(ts_ms / 1000).date().isoformat()


def run_backfill(
    conn: sqlite3.Connection,
    client: CoinGeckoClient,
    years: int,
    universe_size: int = 200,
    extra_coin_ids: list[str] | None = None,
) -> dict:
    """Reconstructs top-50 history by fetching market-cap/price history for today's
    largest `universe_size` coins (plus any `extra_coin_ids`) and re-deriving the
    top 50 day by day. See config/seed_extra_coins.yaml for the survivorship-bias
    caveat this approach carries.
    """
    categories, fallback = load_categories()

    logger.info("Fetching current top %d markets to build backfill universe...", universe_size)
    universe_markets = client.get_top_markets(universe_size)
    coin_meta: dict[str, dict] = {m["id"]: m for m in universe_markets}

    extra_coin_ids = extra_coin_ids or []
    missing_extra = [cid for cid in extra_coin_ids if cid not in coin_meta]
    if missing_extra:
        logger.info("Resolving metadata for %d extra seed coin(s)...", len(missing_extra))
        for m in client.get_coins_by_ids(missing_extra):
            coin_meta[m["id"]] = m

    all_ids = list(coin_meta.keys())
    logger.info("Backfill universe: %d coins", len(all_ids))

    today = datetime.now().date()
    requested_from_date = today - timedelta(days=years * 365)

    # CoinGecko's free tier (with or without a Demo API key) rejects market_chart/range
    # requests older than 365 days -- only paid Pro plans get full historical range.
    # Clamp instead of failing outright; run `backfill` again periodically (or switch to
    # the accumulative `snapshot` command) to build up history beyond what's available now.
    max_history_days = 364
    earliest_allowed = today - timedelta(days=max_history_days)
    from_date = max(requested_from_date, earliest_allowed)
    if from_date > requested_from_date:
        logger.warning(
            "Requested %d years of history, but CoinGecko's free tier only allows the "
            "last %d days via market_chart/range. Backfilling from %s instead of %s. "
            "Run `backfill` again over time, or rely on daily `snapshot` runs, to extend "
            "history further back is not possible without a paid CoinGecko plan.",
            years, max_history_days, from_date.isoformat(), requested_from_date.isoformat(),
        )

    from_ts = int(datetime(from_date.year, from_date.month, from_date.day).timestamp())
    to_ts = int(datetime(today.year, today.month, today.day, 23, 59, 59).timestamp())

    # day_str -> coin_id -> (price, market_cap, volume)
    by_day: dict[str, dict[str, tuple[float, float, float | None]]] = {}

    for i, coin_id in enumerate(all_ids, start=1):
        logger.info("[%d/%d] Fetching history for %s...", i, len(all_ids), coin_id)
        chart = client.get_market_chart_range(coin_id, from_ts, to_ts)
        prices = {(_ms_to_date_str(ts)): price for ts, price in chart.get("prices", [])}
        caps = {(_ms_to_date_str(ts)): cap for ts, cap in chart.get("market_caps", [])}
        volumes = {(_ms_to_date_str(ts)): vol for ts, vol in chart.get("total_volumes", [])}
        for day_str, cap in caps.items():
            price = prices.get(day_str)
            if price is None or cap is None:
                continue
            by_day.setdefault(day_str, {})[coin_id] = (price, cap, volumes.get(day_str))

    if not by_day:
        logger.error("Backfill fetched no data at all -- check API connectivity/key")
        return {"days_processed": 0}

    # Exclude "today": CoinGecko's most recent data point per coin can land at
    # slightly different times of day, so on a still-in-progress day some large
    # coins may simply have no data point yet while smaller ones do -- ranking
    # today from partial data can make major coins look like they "exited".
    # `snapshot` (live top-N endpoint, always a complete/consistent read) is the
    # authoritative source for today; run it right after backfill.
    today_str = today.isoformat()
    if today_str in by_day:
        del by_day[today_str]

    if not by_day:
        logger.warning("No complete historical days to replay after excluding today.")
        return {"days_processed": 0, "universe_size": len(all_ids)}

    logger.info("Replaying %d historical days chronologically...", len(by_day))
    days_processed = 0
    for day_str in sorted(by_day.keys()):
        day_data = by_day[day_str]
        ranked = sorted(day_data.items(), key=lambda kv: kv[1][1], reverse=True)[:50]
        rows = []
        for rank, (coin_id, (price, cap, volume)) in enumerate(ranked, start=1):
            meta = coin_meta.get(coin_id, {})
            rows.append(
                {
                    "coin_id": coin_id,
                    "symbol": meta.get("symbol", coin_id).upper(),
                    "name": meta.get("name", coin_id),
                    "rank": rank,
                    "price_usd": price,
                    "market_cap": cap,
                    "volume_24h": volume,
                }
            )
        apply_snapshot(conn, day_str, rows, categories, fallback)
        days_processed += 1

    logger.info(
        "Backfill complete: %d historical days replayed (through yesterday). "
        "Run `snapshot` now to record today's live top 50.", days_processed,
    )
    return {"days_processed": days_processed, "universe_size": len(all_ids)}
