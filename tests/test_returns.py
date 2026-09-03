import sqlite3
from datetime import date, timedelta

from src import db, returns


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(db.SCHEMA)
    return conn


class FakePriceClient:
    """Stub CoinGeckoClient: returns a fixed price per (coin_id, date) from a dict."""

    def __init__(self, prices: dict[tuple[str, str], float]):
        self.prices = prices
        self.calls: list[tuple[str, str]] = []

    def get_price_on_date(self, coin_id: str, on_date: date) -> float | None:
        key = (coin_id, on_date.isoformat())
        self.calls.append(key)
        return self.prices.get(key)


def seed_coin_and_tenure(conn, coin_id="bitcoin", entry_date=None, entry_price=100.0, exit_date=None, exit_price=None):
    entry_date = entry_date or (date.today() - timedelta(days=300)).isoformat()
    db.upsert_coin(conn, coin_id, coin_id[:3].upper(), coin_id.title(), "Layer 1", entry_date)
    tenure_id = db.open_tenure(conn, coin_id, entry_date, entry_price, entry_rank=1)
    if exit_date:
        db.close_tenure(conn, tenure_id, exit_date, exit_price, days_in_top50=(date.fromisoformat(exit_date) - date.fromisoformat(entry_date)).days)
    conn.commit()
    return tenure_id


def test_return_pct_calculation_is_correct():
    conn = make_conn()
    entry_date = date.today() - timedelta(days=300)
    tenure_id = seed_coin_and_tenure(conn, entry_date=entry_date.isoformat(), entry_price=100.0)

    d20_date = (entry_date + timedelta(days=20)).isoformat()
    client = FakePriceClient({("bitcoin", d20_date): 150.0})

    result = returns.compute_pending_returns(conn, client)

    row = db.get_return(conn, tenure_id, 20)
    assert row is not None
    assert row["price_at_day"] == 150.0
    assert round(row["return_pct"], 4) == 50.0
    assert result["computed"] >= 1


def test_milestones_in_the_future_are_skipped():
    conn = make_conn()
    entry_date = date.today() - timedelta(days=5)  # d20/d50/d100/d200 all in the future
    seed_coin_and_tenure(conn, entry_date=entry_date.isoformat())
    client = FakePriceClient({})

    result = returns.compute_pending_returns(conn, client)

    assert result["computed"] == 0
    assert result["skipped_future"] == 4


def test_returns_are_immutable_once_computed():
    conn = make_conn()
    entry_date = date.today() - timedelta(days=300)
    tenure_id = seed_coin_and_tenure(conn, entry_date=entry_date.isoformat(), entry_price=100.0)
    d20_date = (entry_date + timedelta(days=20)).isoformat()

    client = FakePriceClient({("bitcoin", d20_date): 150.0})
    returns.compute_pending_returns(conn, client)

    # Price "changes" upstream, but a second run must not recompute or re-call the API.
    client2 = FakePriceClient({("bitcoin", d20_date): 999.0})
    result2 = returns.compute_pending_returns(conn, client2)

    row = db.get_return(conn, tenure_id, 20)
    assert row["price_at_day"] == 150.0  # unchanged
    assert result2["already_done"] >= 1


def test_exited_before_milestone_is_flagged():
    conn = make_conn()
    entry_date = date.today() - timedelta(days=300)
    exit_date = entry_date + timedelta(days=10)  # exits before the d20 milestone
    tenure_id = seed_coin_and_tenure(
        conn,
        entry_date=entry_date.isoformat(),
        entry_price=100.0,
        exit_date=exit_date.isoformat(),
        exit_price=110.0,
    )
    d20_date = (entry_date + timedelta(days=20)).isoformat()
    client = FakePriceClient({("bitcoin", d20_date): 130.0})

    returns.compute_pending_returns(conn, client)

    row = db.get_return(conn, tenure_id, 20)
    assert row["exited_before_milestone"] == 1
    # Return is still computed even though the coin had already left the top 50.
    assert round(row["return_pct"], 4) == 30.0


def test_snapshot_price_is_used_before_calling_the_api():
    conn = make_conn()
    entry_date = date.today() - timedelta(days=300)
    tenure_id = seed_coin_and_tenure(conn, entry_date=entry_date.isoformat(), entry_price=100.0)
    d20_date = (entry_date + timedelta(days=20)).isoformat()

    db.insert_snapshot_rows(
        conn, d20_date,
        [{"coin_id": "bitcoin", "rank": 1, "price_usd": 175.0, "market_cap": 1.0, "volume_24h": 1.0}],
    )
    conn.commit()

    # Only d20 has cached data; d50/d100/d200 are also due (entry was 300 days ago)
    # and legitimately fall through to the API -- this test only asserts that d20
    # specifically was resolved from the snapshot without an API call.
    client = FakePriceClient({})
    returns.compute_pending_returns(conn, client)

    row = db.get_return(conn, tenure_id, 20)
    assert row["price_at_day"] == 175.0
    assert ("bitcoin", d20_date) not in client.calls
