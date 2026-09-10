import sqlite3
from datetime import date, timedelta

import pandas as pd

from src import analytics, db


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


def seed_snapshot_series(conn, coin_id, category, start: date, days: int, price_fn):
    seed_coin(conn, coin_id, category, start.isoformat())
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        db.insert_snapshot_rows(conn, d, [
            {"coin_id": coin_id, "rank": 1, "price_usd": price_fn(i), "market_cap": price_fn(i) * 1_000_000, "volume_24h": 0}
        ])
    conn.commit()


def test_btc_benchmark_computes_alpha_from_cached_prices():
    conn = make_conn()
    entry_date = date.today() - timedelta(days=100)
    target_date = entry_date + timedelta(days=20)

    seed_coin(conn, "bitcoin", "Layer 1", entry_date.isoformat())
    seed_coin(conn, "altcoin", "DeFi", entry_date.isoformat())

    # BTC: 100 -> 110 (+10%) over the d20 window.
    db.cache_price(conn, "bitcoin", entry_date.isoformat(), 100.0)
    db.cache_price(conn, "bitcoin", target_date.isoformat(), 110.0)

    tenure_id = db.open_tenure(conn, "altcoin", entry_date.isoformat(), 50.0, entry_rank=10)
    # altcoin: 50 -> 75 (+50%) return, already computed.
    db.save_return(conn, tenure_id, 20, price_at_day=75.0, return_pct=50.0, exited_before_milestone=False)
    conn.commit()

    tenures_view = analytics._build_tenures_view(
        pd.read_sql_query("SELECT * FROM tenures", conn),
        pd.read_sql_query("SELECT * FROM coins", conn),
        date.today(),
    )
    returns_df = pd.read_sql_query("SELECT * FROM returns", conn)

    result = analytics._btc_benchmark(tenures_view, returns_df, conn)

    assert result["20"]["n"] == 1
    assert round(result["20"]["avg_strategy_return"], 1) == 50.0
    assert round(result["20"]["avg_btc_return"], 1) == 10.0
    assert round(result["20"]["alpha"], 1) == 40.0


def test_btc_benchmark_excludes_btcs_own_tenure():
    conn = make_conn()
    entry_date = date.today() - timedelta(days=100)
    target_date = entry_date + timedelta(days=20)

    seed_coin(conn, "bitcoin", "Layer 1", entry_date.isoformat())
    db.cache_price(conn, "bitcoin", entry_date.isoformat(), 100.0)
    db.cache_price(conn, "bitcoin", target_date.isoformat(), 110.0)

    tenure_id = db.open_tenure(conn, "bitcoin", entry_date.isoformat(), 100.0, entry_rank=1)
    db.save_return(conn, tenure_id, 20, price_at_day=110.0, return_pct=10.0, exited_before_milestone=False)
    conn.commit()

    tenures_view = analytics._build_tenures_view(
        pd.read_sql_query("SELECT * FROM tenures", conn),
        pd.read_sql_query("SELECT * FROM coins", conn),
        date.today(),
    )
    returns_df = pd.read_sql_query("SELECT * FROM returns", conn)

    result = analytics._btc_benchmark(tenures_view, returns_df, conn)

    # No non-BTC tenures to compare -- BTC vs itself is meaningless and must be excluded.
    assert result["20"]["n"] == 0


def test_altcoin_season_index_excludes_btc_and_stablecoins_from_candidates():
    conn = make_conn()
    start = date(2026, 1, 1)
    seed_snapshot_series(conn, "bitcoin", "Layer 1", start, 100, lambda i: 100.0)
    seed_snapshot_series(conn, "tether", "Stablecoin", start, 100, lambda i: 1.0)
    seed_snapshot_series(conn, "somealt", "DeFi", start, 100, lambda i: 100.0 + i)

    snapshots = pd.read_sql_query("SELECT * FROM snapshots", conn)
    coins = pd.read_sql_query("SELECT * FROM coins", conn)
    result = analytics._altcoin_season_index(snapshots, coins)

    # Only "somealt" is an eligible candidate -- BTC is the benchmark, not a
    # candidate, and stablecoins are excluded the same way they are elsewhere.
    assert result["history"][-1]["n"] == 1


def test_altcoin_season_index_computes_pct_beating_btc():
    conn = make_conn()
    start = date(2026, 1, 1)
    days = 100

    seed_snapshot_series(conn, "bitcoin", "Layer 1", start, days, lambda i: 100.0)
    for n in range(6):
        seed_snapshot_series(conn, f"winner{n}", "DeFi", start, days, lambda i: 110.0 if i == days - 1 else 100.0)
    for n in range(4):
        seed_snapshot_series(conn, f"loser{n}", "DeFi", start, days, lambda i: 95.0 if i == days - 1 else 100.0)

    snapshots = pd.read_sql_query("SELECT * FROM snapshots", conn)
    coins = pd.read_sql_query("SELECT * FROM coins", conn)
    result = analytics._altcoin_season_index(snapshots, coins)

    last = result["history"][-1]
    assert last["n"] == 10
    assert last["pct_beating_btc"] == 60.0


def test_altcoin_season_index_withholds_pct_below_min_coins():
    conn = make_conn()
    start = date(2026, 1, 1)
    days = 100
    seed_snapshot_series(conn, "bitcoin", "Layer 1", start, days, lambda i: 100.0)
    for n in range(5):
        seed_snapshot_series(conn, f"alt{n}", "DeFi", start, days, lambda i: 100.0 + i)

    snapshots = pd.read_sql_query("SELECT * FROM snapshots", conn)
    coins = pd.read_sql_query("SELECT * FROM coins", conn)
    result = analytics._altcoin_season_index(snapshots, coins)

    last = result["history"][-1]
    assert last["n"] == 5
    assert last["pct_beating_btc"] is None
