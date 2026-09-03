"""Return calculation at fixed milestones (d20/d50/d100/d200) with price caching.

Computed returns are immutable: once a (tenure_id, milestone_day) row exists it is
never recalculated (db.save_return is a no-op on conflict).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from src import db
from src.coingecko import CoinGeckoClient
from src.config import MILESTONES

logger = logging.getLogger(__name__)


def _get_price_for_date(conn: sqlite3.Connection, client: CoinGeckoClient, coin_id: str, target_date: date) -> float | None:
    date_str = target_date.isoformat()

    cached = db.get_cached_price(conn, coin_id, date_str)
    if cached is not None:
        return cached

    # Opportunistic: we may already have this exact price from a snapshot (free, no API call).
    row = conn.execute(
        "SELECT price_usd FROM snapshots WHERE snapshot_date = ? AND coin_id = ?",
        (date_str, coin_id),
    ).fetchone()
    if row and row["price_usd"] is not None:
        db.cache_price(conn, coin_id, date_str, row["price_usd"])
        return row["price_usd"]

    price = client.get_price_on_date(coin_id, target_date)
    if price is not None:
        db.cache_price(conn, coin_id, date_str, price)
    return price


def compute_pending_returns(conn: sqlite3.Connection, client: CoinGeckoClient) -> dict:
    """Computes all missing (tenure, milestone) returns whose target date has passed."""
    today = date.today()
    tenures = conn.execute("SELECT * FROM tenures").fetchall()

    computed = 0
    skipped_future = 0
    skipped_no_price = 0
    already_done = 0

    for tenure in tenures:
        entry_date = date.fromisoformat(tenure["entry_date"])
        exit_date = date.fromisoformat(tenure["exit_date"]) if tenure["exit_date"] else None

        for milestone in MILESTONES:
            target_date = entry_date + timedelta(days=milestone)
            if target_date > today:
                skipped_future += 1
                continue

            if db.get_return(conn, tenure["tenure_id"], milestone) is not None:
                already_done += 1
                continue

            price = _get_price_for_date(conn, client, tenure["coin_id"], target_date)
            if price is None:
                skipped_no_price += 1
                logger.warning(
                    "No price for %s on %s (tenure %d, d%d) -- skipping",
                    tenure["coin_id"], target_date.isoformat(), tenure["tenure_id"], milestone,
                )
                continue

            return_pct = ((price - tenure["entry_price"]) / tenure["entry_price"]) * 100
            exited_before = exit_date is not None and exit_date < target_date

            db.save_return(
                conn,
                tenure_id=tenure["tenure_id"],
                milestone_day=milestone,
                price_at_day=price,
                return_pct=return_pct,
                exited_before_milestone=exited_before,
            )
            computed += 1

        conn.commit()

    logger.info(
        "Returns: %d computed, %d already done, %d future (skipped), %d missing price",
        computed, already_done, skipped_future, skipped_no_price,
    )
    return {
        "computed": computed,
        "already_done": already_done,
        "skipped_future": skipped_future,
        "skipped_no_price": skipped_no_price,
    }
