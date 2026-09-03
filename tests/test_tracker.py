import sqlite3

from src import db, tracker

CATEGORIES = {"bitcoin": "Layer 1", "ethereum": "Layer 1", "tether": "Stablecoin", "solana": "Layer 1"}


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(db.SCHEMA)
    return conn


def row(coin_id: str, rank: int, price: float) -> dict:
    return {
        "coin_id": coin_id,
        "symbol": coin_id[:3].upper(),
        "name": coin_id.title(),
        "rank": rank,
        "price_usd": price,
        "market_cap": price * 1_000_000,
        "volume_24h": 1000.0,
    }


def test_first_snapshot_opens_a_tenure_per_coin():
    conn = make_conn()
    rows = [row("bitcoin", 1, 60000), row("ethereum", 2, 3000)]

    result = tracker.apply_snapshot(conn, "2024-01-01", rows, CATEGORIES, "Other")

    assert result["entries"] == ["bitcoin", "ethereum"]
    assert result["exits"] == []
    tenures = conn.execute("SELECT * FROM tenures").fetchall()
    assert len(tenures) == 2
    assert all(t["exit_date"] is None for t in tenures)
    assert all(t["entry_date"] == "2024-01-01" for t in tenures)


def test_continuing_coin_updates_days_without_new_tenure():
    conn = make_conn()
    tracker.apply_snapshot(conn, "2024-01-01", [row("bitcoin", 1, 60000)], CATEGORIES, "Other")
    tracker.apply_snapshot(conn, "2024-01-03", [row("bitcoin", 1, 61000)], CATEGORIES, "Other")

    tenures = conn.execute("SELECT * FROM tenures").fetchall()
    assert len(tenures) == 1
    assert tenures[0]["days_in_top50"] == 2
    assert tenures[0]["exit_date"] is None


def test_exit_closes_tenure_with_previous_days_price():
    conn = make_conn()
    tracker.apply_snapshot(conn, "2024-01-01", [row("bitcoin", 1, 60000), row("ethereum", 2, 3000)], CATEGORIES, "Other")
    tracker.apply_snapshot(conn, "2024-01-02", [row("bitcoin", 1, 61000)], CATEGORIES, "Other")

    eth = conn.execute("SELECT * FROM tenures WHERE coin_id = 'ethereum'").fetchone()
    assert eth["exit_date"] == "2024-01-01"
    assert eth["exit_price"] == 3000
    assert eth["days_in_top50"] == 0


def test_reentry_after_exit_opens_a_second_tenure():
    conn = make_conn()
    tracker.apply_snapshot(conn, "2024-01-01", [row("bitcoin", 1, 60000), row("ethereum", 2, 3000)], CATEGORIES, "Other")
    tracker.apply_snapshot(conn, "2024-01-02", [row("bitcoin", 1, 61000)], CATEGORIES, "Other")
    tracker.apply_snapshot(conn, "2024-01-03", [row("bitcoin", 1, 62000), row("ethereum", 2, 3100)], CATEGORIES, "Other")

    eth_tenures = conn.execute(
        "SELECT * FROM tenures WHERE coin_id = 'ethereum' ORDER BY entry_date"
    ).fetchall()
    assert len(eth_tenures) == 2
    assert eth_tenures[0]["exit_date"] == "2024-01-01"
    assert eth_tenures[1]["entry_date"] == "2024-01-03"
    assert eth_tenures[1]["exit_date"] is None


def test_running_snapshot_twice_same_day_does_not_duplicate_tenures():
    conn = make_conn()
    rows = [row("bitcoin", 1, 60000), row("ethereum", 2, 3000)]

    tracker.apply_snapshot(conn, "2024-01-01", rows, CATEGORIES, "Other")
    second = tracker.apply_snapshot(conn, "2024-01-01", rows, CATEGORIES, "Other")

    assert second["already_recorded"] is True
    tenures = conn.execute("SELECT * FROM tenures").fetchall()
    assert len(tenures) == 2  # not 4


def test_running_snapshot_twice_then_a_new_day_diffs_correctly():
    conn = make_conn()
    tracker.apply_snapshot(conn, "2024-01-01", [row("bitcoin", 1, 60000), row("ethereum", 2, 3000)], CATEGORIES, "Other")
    tracker.apply_snapshot(conn, "2024-01-01", [row("bitcoin", 1, 60000), row("ethereum", 2, 3000)], CATEGORIES, "Other")
    result = tracker.apply_snapshot(conn, "2024-01-02", [row("bitcoin", 1, 61000)], CATEGORIES, "Other")

    assert result["exits"] == ["ethereum"]
    tenures = conn.execute("SELECT * FROM tenures").fetchall()
    assert len(tenures) == 2


def test_gap_between_snapshots_is_flagged():
    conn = make_conn()
    tracker.apply_snapshot(conn, "2024-01-01", [row("bitcoin", 1, 60000)], CATEGORIES, "Other")
    result = tracker.apply_snapshot(conn, "2024-01-10", [row("bitcoin", 1, 65000)], CATEGORIES, "Other")

    assert result["gap_days"] == 8
    tenure = conn.execute("SELECT * FROM tenures WHERE coin_id = 'bitcoin'").fetchone()
    assert tenure["has_gap"] == 1


def test_uncategorized_coin_falls_back_and_is_reported():
    conn = make_conn()
    result = tracker.apply_snapshot(conn, "2024-01-01", [row("mystery-coin", 1, 1.0)], CATEGORIES, "Other")

    assert any("mystery-coin" in u for u in result["uncategorized"])
    coin = conn.execute("SELECT * FROM coins WHERE coin_id = 'mystery-coin'").fetchone()
    assert coin["category"] == "Other"
