import sqlite3
from datetime import date, timedelta

import pandas as pd

from src import btc_indicators, db


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(db.SCHEMA)
    return conn


def seed_coin(conn, coin_id, category, entry_date):
    conn.execute(
        "INSERT INTO coins (coin_id, symbol, name, category, first_seen) VALUES (?, ?, ?, ?, ?)",
        (coin_id, coin_id[:3].upper(), coin_id.title(), category, entry_date),
    )


def seed_btc_price_series(conn, start: date, days: int, price_fn) -> None:
    rows = [
        ((start + timedelta(days=i)).isoformat(), price_fn(i))
        for i in range(days)
    ]
    conn.executemany("INSERT INTO btc_price_history (price_date, price_usd) VALUES (?, ?)", rows)
    conn.commit()


def test_rainbow_bands_classifies_todays_price_into_exactly_one_band():
    conn = make_conn()
    # Smooth exponential-ish growth so the log-log fit is well-behaved.
    seed_btc_price_series(conn, date(2015, 1, 1), 400, lambda i: 100.0 * (1.01 ** i))
    df = btc_indicators._load_btc_price_df(conn)

    result = btc_indicators.rainbow_bands(df)

    assert result is not None
    assert 0 <= result["current_band_index"] < len(btc_indicators.RAINBOW_BAND_LABELS)
    assert result["current_band_label"] == btc_indicators.RAINBOW_BAND_LABELS[result["current_band_index"]]
    assert len(result["price_series"]) == len(df)
    assert len(result["band_edges"]) == len(btc_indicators.RAINBOW_BAND_LABELS) + 1


def test_rainbow_bands_returns_none_when_not_enough_history():
    conn = make_conn()
    seed_btc_price_series(conn, date(2026, 1, 1), 10, lambda i: 100.0 + i)
    df = btc_indicators._load_btc_price_df(conn)

    assert btc_indicators.rainbow_bands(df) is None


def test_regime_status_above_both_smas_is_labeled_a_bull_regime():
    conn = make_conn()
    # 400 flat days at 100, then a recent runup -- price ends up above both SMAs.
    seed_btc_price_series(conn, date(2025, 1, 1), 400, lambda i: 100.0 if i < 395 else 100.0 + (i - 394) * 50)
    df = btc_indicators._load_btc_price_df(conn)

    result = btc_indicators.regime_status(df)

    assert result["current_price"] > result["sma"]["200"]
    assert result["current_price"] > result["sma"]["350"]
    assert result["label"] == "Above both moving averages"


def test_regime_status_below_both_smas_is_labeled_a_bear_regime():
    conn = make_conn()
    seed_btc_price_series(conn, date(2025, 1, 1), 400, lambda i: 100.0 if i < 395 else 100.0 - (i - 394) * 5)
    df = btc_indicators._load_btc_price_df(conn)

    result = btc_indicators.regime_status(df)

    assert result["current_price"] < result["sma"]["200"]
    assert result["current_price"] < result["sma"]["350"]
    assert result["label"] == "Below both moving averages"


def test_regime_status_drawdown_from_all_time_high():
    conn = make_conn()
    # Peaks at 200 on day 10, ends at 100 -- a clean 50% drawdown from ATH.
    seed_btc_price_series(conn, date(2026, 1, 1), 20, lambda i: 200.0 if i == 10 else 100.0)
    df = btc_indicators._load_btc_price_df(conn)

    result = btc_indicators.regime_status(df)

    assert result["all_time_high"] == 200.0
    assert result["drawdown_from_ath_pct"] == -50.0


def test_stablecoin_supply_trend_sums_only_stablecoin_category():
    conn = make_conn()
    today = date.today().isoformat()
    seed_coin(conn, "tether", "Stablecoin", today)
    seed_coin(conn, "usd-coin", "Stablecoin", today)
    seed_coin(conn, "bitcoin", "Layer 1", today)

    db.insert_snapshot_rows(conn, today, [
        {"coin_id": "tether", "rank": 3, "price_usd": 1.0, "market_cap": 100_000.0, "volume_24h": 0},
        {"coin_id": "usd-coin", "rank": 5, "price_usd": 1.0, "market_cap": 50_000.0, "volume_24h": 0},
        {"coin_id": "bitcoin", "rank": 1, "price_usd": 60_000.0, "market_cap": 1_200_000_000.0, "volume_24h": 0},
    ])
    conn.commit()

    result = btc_indicators.stablecoin_supply_trend(conn)

    assert len(result) == 1
    assert result[0]["date"] == today
    assert result[0]["total_market_cap_usd"] == 150_000.0
