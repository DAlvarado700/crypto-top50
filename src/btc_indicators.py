"""Bitcoin macro/position indicators: rainbow chart, bull/bear regime, dominance,
fear & greed, and stablecoin supply trend.

Kept separate from analytics.py since this is a distinct concern (BTC's own
market position) from top-50 tenure tracking. Fetching and computing are split
the same way returns/analytics already are: fetch_and_cache() is the only place
that makes network calls; compute() only reads from the DB, matching the
convention _btc_benchmark() follows in analytics.py.

BTC's long price history comes from Yahoo Finance's chart endpoint, not
CoinGecko. CoinGecko's free tier rejects any request for data older than 365
days -- even /coins/bitcoin/market_chart?days=max for a single coin gets
error_code 10012, "Public API users are limited to querying historical data
within the past 365 days" (confirmed by an actual failed call, this isn't
guesswork). CoinDesk's free historical BTC API, the other obvious candidate, no
longer resolves. Yahoo's endpoint is unofficial and undocumented (the same one
the popular `yfinance` library uses under the hood), so it could change or
start blocking without notice, but it's the only free source that actually
returns multi-year daily BTC prices, and it only affects this one feature if it
ever breaks.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

from src.coingecko import CoinGeckoClient, unix_ts

logger = logging.getLogger(__name__)

FEAR_GREED_URL = "https://api.alternative.me/fng/"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD"
YAHOO_CHUNK_DAYS = 700
YAHOO_EARLIEST_GUESS = date(2010, 1, 1)  # earlier than BTC-USD actually starts on Yahoo; API clips it

SMA_WINDOWS = (200, 350)
RAINBOW_BAND_LABELS = [
    "Fire sale", "Buy", "Accumulate", "Still cheap", "Hold",
    "Is this a bubble?", "FOMO intensifies", "Sell. Seriously, sell.", "Maximum bubble",
]
RAINBOW_BAND_HALF_SPAN = 0.5  # total band spread, in log10(price) decades, around the fitted line


# ---------------------------------------------------------------------------
# Fetching (network calls live here only)
# ---------------------------------------------------------------------------

def _fetch_yahoo_chunk(period1: int, period2: int) -> list[tuple[str, float]]:
    resp = requests.get(
        YAHOO_CHART_URL,
        params={"period1": period1, "period2": period2, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15.0,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"]
    if not result:
        return []
    timestamps = result[0].get("timestamp") or []
    closes = result[0]["indicators"]["quote"][0].get("close") or []
    rows = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.utcfromtimestamp(ts).date().isoformat()
        rows.append((d, float(close)))
    return rows


def _fetch_btc_history_yahoo(start: date, end: date) -> list[tuple[str, float]]:
    """Fetches BTC-USD daily closes in chunks -- a single request spanning many
    years gets silently coarsened to non-daily intervals by Yahoo (confirmed
    empirically: days=max/range=max style requests return ~monthly points over
    a decade), so this splits the range into ~2-year windows instead."""
    all_rows: dict[str, float] = {}
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=YAHOO_CHUNK_DAYS), end)
        try:
            rows = _fetch_yahoo_chunk(unix_ts(chunk_start), unix_ts(chunk_end))
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            logger.warning("Yahoo BTC history chunk %s..%s failed: %s", chunk_start, chunk_end, exc)
            rows = []
        for d, close in rows:
            all_rows[d] = close
        chunk_start = chunk_end
        if chunk_start < end:
            time.sleep(0.5)
    return sorted(all_rows.items())


def _fetch_fear_greed_history(limit: int = 0) -> list[tuple[str, int, str | None]]:
    """limit=0 means full history (used once, on an empty table); otherwise the
    most recent `limit` days -- mirrors the incremental approach used for BTC
    price history instead of re-pulling and re-upserting years of rows daily."""
    resp = requests.get(FEAR_GREED_URL, params={"limit": limit, "format": "json"}, timeout=15.0)
    resp.raise_for_status()
    rows = []
    for entry in resp.json().get("data") or []:
        d = datetime.utcfromtimestamp(int(entry["timestamp"])).date().isoformat()
        rows.append((d, int(entry["value"]), entry.get("value_classification")))
    return rows


def _find_missing_date_ranges(existing_dates: set[str], start: date, end: date) -> list[tuple[date, date]]:
    """Contiguous [start, end] date ranges missing from existing_dates -- used to
    self-heal gaps left by a chunk that failed on a previous run (fetch_and_cache
    only ever looks a few days back from the latest cached date otherwise, so a
    gap further back would never get retried without this)."""
    missing: list[tuple[date, date]] = []
    gap_start: date | None = None
    d = start
    while d <= end:
        if d.isoformat() not in existing_dates:
            gap_start = gap_start or d
        elif gap_start is not None:
            missing.append((gap_start, d - timedelta(days=1)))
            gap_start = None
        d += timedelta(days=1)
    if gap_start is not None:
        missing.append((gap_start, end))
    return missing


def fetch_and_cache(conn: sqlite3.Connection, client: CoinGeckoClient) -> dict:
    """Fetches everything compute() needs and caches it in the DB. Safe to call
    daily: BTC price history only re-fetches from a few days before its latest
    cached date forward (or the full range on an empty table) plus a gap-healing
    pass (below), Fear & Greed only re-fetches a small recent window after the
    first run, and the global snapshot is one row per day."""
    summary: dict = {}

    row = conn.execute("SELECT MIN(price_date), MAX(price_date) FROM btc_price_history").fetchone()
    has_history = bool(row and row[1] is not None)
    start = date.fromisoformat(row[1]) - timedelta(days=5) if has_history else YAHOO_EARLIEST_GUESS
    today = date.today()
    yahoo_rows = _fetch_btc_history_yahoo(start, today)
    if yahoo_rows:
        conn.executemany(
            "INSERT INTO btc_price_history (price_date, price_usd) VALUES (?, ?) "
            "ON CONFLICT(price_date) DO UPDATE SET price_usd = excluded.price_usd",
            yahoo_rows,
        )
        conn.commit()
    summary["btc_price_rows_upserted"] = len(yahoo_rows)

    # Self-heal: a chunk that failed on a previous run (network blip, Yahoo
    # rate-limit) would otherwise leave a permanent gap, since the incremental
    # fetch above only ever looks a few days back from the latest cached date.
    gap_rows_recovered = 0
    if has_history:
        earliest = date.fromisoformat(row[0])
        existing_dates = {r[0] for r in conn.execute("SELECT price_date FROM btc_price_history").fetchall()}
        gaps = _find_missing_date_ranges(existing_dates, earliest, today)
        for gap_start, gap_end in gaps:
            recovered = _fetch_btc_history_yahoo(gap_start, gap_end + timedelta(days=1))
            if recovered:
                conn.executemany(
                    "INSERT INTO btc_price_history (price_date, price_usd) VALUES (?, ?) "
                    "ON CONFLICT(price_date) DO UPDATE SET price_usd = excluded.price_usd",
                    recovered,
                )
                conn.commit()
                gap_rows_recovered += len(recovered)
        summary["gap_ranges_found"] = len(gaps)
    summary["gap_rows_recovered"] = gap_rows_recovered

    global_data = client.get_global()
    gd = (global_data or {}).get("data")
    if gd:
        today = date.today().isoformat()
        total_mcap = (gd.get("total_market_cap") or {}).get("usd")
        btc_dom = (gd.get("market_cap_percentage") or {}).get("btc")
        conn.execute(
            "INSERT INTO global_snapshots (snapshot_date, total_market_cap_usd, btc_dominance_pct) "
            "VALUES (?, ?, ?) ON CONFLICT(snapshot_date) DO UPDATE SET "
            "total_market_cap_usd = excluded.total_market_cap_usd, btc_dominance_pct = excluded.btc_dominance_pct",
            (today, total_mcap, btc_dom),
        )
        conn.commit()
    summary["global_snapshot_recorded"] = bool(gd)

    has_fg_history = conn.execute("SELECT 1 FROM fear_greed_history LIMIT 1").fetchone() is not None
    try:
        fg_rows = _fetch_fear_greed_history(limit=0 if not has_fg_history else 30)
    except (requests.RequestException, KeyError, ValueError) as exc:
        logger.warning("Fear & Greed fetch failed: %s", exc)
        fg_rows = []
    if fg_rows:
        conn.executemany(
            "INSERT INTO fear_greed_history (fg_date, value, classification) VALUES (?, ?, ?) "
            "ON CONFLICT(fg_date) DO UPDATE SET value = excluded.value, classification = excluded.classification",
            fg_rows,
        )
        conn.commit()
    summary["fear_greed_rows_upserted"] = len(fg_rows)

    return summary


# ---------------------------------------------------------------------------
# Computing (DB reads only, no network calls)
# ---------------------------------------------------------------------------

def _load_btc_price_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT price_date, price_usd FROM btc_price_history WHERE price_usd IS NOT NULL ORDER BY price_date",
        conn,
    )


RAINBOW_CURVE_STEP = 7  # fit line / band edges are smooth curves -- weekly points are plenty
FEAR_GREED_EXPORT_DAYS = 400  # dashboard only ever plots the last 180; this leaves headroom


def _downsample(items: list, step: int) -> list:
    """Every `step`-th item, always keeping the last one so the curve's right
    edge still lines up with today's date/price."""
    if step <= 1 or len(items) <= step:
        return items
    sampled = items[::step]
    if (len(items) - 1) % step != 0:
        sampled = sampled + [items[-1]]
    return sampled


def rainbow_bands(df: pd.DataFrame) -> dict | None:
    """Least-squares fit of log10(price) vs log10(days since first cached point).
    Bands are fixed, evenly spaced offsets in log space around the fitted line --
    self-fit from our own data, not copied published band constants, so this is
    a modeling choice rather than a canonical reference chart."""
    if len(df) < 90:
        return None

    dates = df["price_date"].tolist()
    prices = df["price_usd"].to_numpy(dtype=float)
    first_date = pd.to_datetime(dates[0])
    days_since_start = (pd.to_datetime(dates) - first_date).days.to_numpy()
    days_since_start = np.where(days_since_start <= 0, 1, days_since_start)

    log_x = np.log10(days_since_start)
    log_y = np.log10(prices)
    slope, intercept = np.polyfit(log_x, log_y, 1)
    fitted_log = slope * log_x + intercept

    n_edges = len(RAINBOW_BAND_LABELS) + 1
    offsets = np.linspace(-RAINBOW_BAND_HALF_SPAN, RAINBOW_BAND_HALF_SPAN, n_edges)

    # price_series stays at full daily resolution (it's the real data, one series).
    # fit_line and each band edge are smooth mathematical curves derived from the
    # same regression, so weekly points are visually identical but ~7x smaller --
    # with a decade+ of daily history and 10 band edges, the full-resolution
    # version bloated dashboard/data.js to several megabytes for no visible gain.
    price_series = [{"date": d, "value": round(float(p), 2)} for d, p in zip(dates, prices)]
    fit_line = _downsample(
        [{"date": d, "value": round(float(10 ** fl), 2)} for d, fl in zip(dates, fitted_log)],
        RAINBOW_CURVE_STEP,
    )
    band_edges = [
        _downsample(
            [{"date": d, "value": round(float(10 ** (fl + off)), 2)} for d, fl in zip(dates, fitted_log)],
            RAINBOW_CURVE_STEP,
        )
        for off in offsets
    ]

    current_offset = log_y[-1] - fitted_log[-1]
    band_idx = int(np.clip(np.searchsorted(offsets, current_offset) - 1, 0, len(RAINBOW_BAND_LABELS) - 1))

    return {
        "price_series": price_series,
        "fit_line": fit_line,
        "band_edges": band_edges,
        "band_labels": RAINBOW_BAND_LABELS,
        "current_band_index": band_idx,
        "current_band_label": RAINBOW_BAND_LABELS[band_idx],
        "history_start": dates[0],
        "history_end": dates[-1],
    }


def regime_status(df: pd.DataFrame) -> dict | None:
    """Price vs SMA200/SMA350, plus % drawdown from all-time high. A label is
    included for convenience, but the raw numbers are the point -- this reports
    where BTC currently sits, it doesn't predict where it's going."""
    if df.empty:
        return None

    prices = df["price_usd"]
    current_price = float(prices.iloc[-1])
    all_time_high = float(prices.max())
    drawdown_pct = round((current_price - all_time_high) / all_time_high * 100, 2)

    smas = {}
    for window in SMA_WINDOWS:
        smas[window] = float(prices.tail(window).mean()) if len(prices) >= window else None

    above = [smas[w] is not None and current_price > smas[w] for w in SMA_WINDOWS]
    if all(above):
        label = "Above both moving averages"
    elif not any(above):
        label = "Below both moving averages"
    else:
        label = "Mixed -- between moving averages"

    return {
        "current_price": round(current_price, 2),
        "all_time_high": round(all_time_high, 2),
        "drawdown_from_ath_pct": drawdown_pct,
        "sma": {str(w): (round(v, 2) if v is not None else None) for w, v in smas.items()},
        "label": label,
        "as_of": df["price_date"].iloc[-1],
    }


def fear_greed_summary(conn: sqlite3.Connection) -> dict | None:
    """Exports only the most recent FEAR_GREED_EXPORT_DAYS days -- the dashboard
    only ever plots the last 180, and the full history (daily since 2018, only
    growing) doesn't need to ship to the browser every time. The DB still keeps
    everything, so a bigger window later just means changing this constant."""
    rows = conn.execute(
        "SELECT fg_date, value, classification FROM fear_greed_history "
        "ORDER BY fg_date DESC LIMIT ?",
        (FEAR_GREED_EXPORT_DAYS,),
    ).fetchall()
    if not rows:
        return None
    history = [
        {"date": r["fg_date"], "value": r["value"], "classification": r["classification"]}
        for r in reversed(rows)
    ]
    latest = history[-1]
    return {"current": latest, "history": history}


def dominance_trend(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT snapshot_date, total_market_cap_usd, btc_dominance_pct "
        "FROM global_snapshots ORDER BY snapshot_date"
    ).fetchall()
    history = [
        {
            "date": r["snapshot_date"],
            "total_market_cap_usd": r["total_market_cap_usd"],
            "btc_dominance_pct": r["btc_dominance_pct"],
        }
        for r in rows
    ]
    return {
        "history": history,
        "note": "No free historical source exists for this -- it accumulates one point per day from when this shipped.",
    }


def stablecoin_supply_trend(conn: sqlite3.Connection) -> list[dict]:
    """Aggregate stablecoin market cap per snapshot date, as a rough 'money on
    the sidelines' liquidity proxy. Needs no new fetching -- fully derivable from
    snapshots/coins data already collected for the top-50 tracker."""
    rows = conn.execute(
        "SELECT s.snapshot_date AS d, SUM(s.market_cap) AS total "
        "FROM snapshots s JOIN coins c ON c.coin_id = s.coin_id "
        "WHERE c.category = 'Stablecoin' "
        "GROUP BY s.snapshot_date ORDER BY s.snapshot_date"
    ).fetchall()
    return [{"date": r["d"], "total_market_cap_usd": r["total"]} for r in rows]


def compute(conn: sqlite3.Connection) -> dict:
    """Builds the btc_indicators dict merged into analytics.build_analysis()'s
    output. Reads only from the DB -- fetch_and_cache() must run first (wired
    into `main.py run` ahead of `analyze`)."""
    btc_df = _load_btc_price_df(conn)
    return {
        "rainbow": rainbow_bands(btc_df),
        "regime": regime_status(btc_df),
        "fear_greed": fear_greed_summary(conn),
        "dominance": dominance_trend(conn),
        "stablecoin_supply": stablecoin_supply_trend(conn),
    }
