"""SQLite schema and query/write helpers for the tracker database."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from src.config import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS coins (
    coin_id      TEXT PRIMARY KEY,
    symbol       TEXT NOT NULL,
    name         TEXT NOT NULL,
    category     TEXT,
    first_seen   DATE
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_date DATE NOT NULL,
    coin_id       TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    price_usd     REAL,
    market_cap    REAL,
    volume_24h    REAL,
    PRIMARY KEY (snapshot_date, coin_id),
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

CREATE TABLE IF NOT EXISTS tenures (
    tenure_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    coin_id       TEXT NOT NULL,
    entry_date    DATE NOT NULL,
    entry_price   REAL NOT NULL,
    entry_rank    INTEGER,
    exit_date     DATE,
    exit_price    REAL,
    days_in_top50 INTEGER,
    has_gap       INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

CREATE TABLE IF NOT EXISTS returns (
    tenure_id             INTEGER NOT NULL,
    milestone_day         INTEGER NOT NULL,
    price_at_day          REAL,
    return_pct            REAL,
    exited_before_milestone INTEGER NOT NULL DEFAULT 0,
    computed_at           TIMESTAMP,
    PRIMARY KEY (tenure_id, milestone_day),
    FOREIGN KEY (tenure_id) REFERENCES tenures(tenure_id)
);

CREATE TABLE IF NOT EXISTS price_cache (
    coin_id    TEXT NOT NULL,
    price_date DATE NOT NULL,
    price_usd  REAL,
    PRIMARY KEY (coin_id, price_date)
);

CREATE TABLE IF NOT EXISTS snapshot_log (
    snapshot_date TEXT PRIMARY KEY,
    recorded_at   TIMESTAMP NOT NULL,
    gap_days      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snapshots_coin_id ON snapshots(coin_id);
CREATE INDEX IF NOT EXISTS idx_tenures_coin_exit ON tenures(coin_id, exit_date);
CREATE INDEX IF NOT EXISTS idx_price_cache_coin ON price_cache(coin_id);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Creates the database file and schema if they don't already exist."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        logger.info("Database initialized at %s", db_path)
    finally:
        conn.close()


def upsert_coin(
    conn: sqlite3.Connection,
    coin_id: str,
    symbol: str,
    name: str,
    category: str | None,
    first_seen: str,
) -> None:
    conn.execute(
        """
        INSERT INTO coins (coin_id, symbol, name, category, first_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(coin_id) DO UPDATE SET
            symbol = excluded.symbol,
            name = excluded.name,
            category = excluded.category
        """,
        (coin_id, symbol, name, category, first_seen),
    )


def insert_snapshot_rows(conn: sqlite3.Connection, snapshot_date: str, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO snapshots (snapshot_date, coin_id, rank, price_usd, market_cap, volume_24h)
        VALUES (:snapshot_date, :coin_id, :rank, :price_usd, :market_cap, :volume_24h)
        ON CONFLICT(snapshot_date, coin_id) DO UPDATE SET
            rank = excluded.rank,
            price_usd = excluded.price_usd,
            market_cap = excluded.market_cap,
            volume_24h = excluded.volume_24h
        """,
        [{**row, "snapshot_date": snapshot_date} for row in rows],
    )


def get_latest_snapshot_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(snapshot_date) AS d FROM snapshots").fetchone()
    return row["d"] if row and row["d"] else None


def get_snapshot_coin_ids(conn: sqlite3.Connection, snapshot_date: str) -> set[str]:
    rows = conn.execute(
        "SELECT coin_id FROM snapshots WHERE snapshot_date = ?", (snapshot_date,)
    ).fetchall()
    return {r["coin_id"] for r in rows}


def get_open_tenure(conn: sqlite3.Connection, coin_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tenures WHERE coin_id = ? AND exit_date IS NULL",
        (coin_id,),
    ).fetchone()


def open_tenure(
    conn: sqlite3.Connection,
    coin_id: str,
    entry_date: str,
    entry_price: float,
    entry_rank: int,
    has_gap: bool = False,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO tenures (coin_id, entry_date, entry_price, entry_rank, days_in_top50, has_gap)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (coin_id, entry_date, entry_price, entry_rank, int(has_gap)),
    )
    return cur.lastrowid


def close_tenure(
    conn: sqlite3.Connection, tenure_id: int, exit_date: str, exit_price: float, days_in_top50: int
) -> None:
    conn.execute(
        """
        UPDATE tenures
        SET exit_date = ?, exit_price = ?, days_in_top50 = ?
        WHERE tenure_id = ?
        """,
        (exit_date, exit_price, days_in_top50, tenure_id),
    )


def update_tenure_days(conn: sqlite3.Connection, tenure_id: int, days_in_top50: int) -> None:
    conn.execute(
        "UPDATE tenures SET days_in_top50 = ? WHERE tenure_id = ?",
        (days_in_top50, tenure_id),
    )


def mark_tenure_gap(conn: sqlite3.Connection, tenure_id: int) -> None:
    conn.execute("UPDATE tenures SET has_gap = 1 WHERE tenure_id = ?", (tenure_id,))


def log_snapshot(conn: sqlite3.Connection, snapshot_date: str, gap_days: int) -> None:
    conn.execute(
        """
        INSERT INTO snapshot_log (snapshot_date, recorded_at, gap_days)
        VALUES (?, datetime('now'), ?)
        ON CONFLICT(snapshot_date) DO UPDATE SET gap_days = excluded.gap_days
        """,
        (snapshot_date, gap_days),
    )


def get_cached_price(conn: sqlite3.Connection, coin_id: str, price_date: str) -> float | None:
    row = conn.execute(
        "SELECT price_usd FROM price_cache WHERE coin_id = ? AND price_date = ?",
        (coin_id, price_date),
    ).fetchone()
    return row["price_usd"] if row else None


def cache_price(conn: sqlite3.Connection, coin_id: str, price_date: str, price_usd: float) -> None:
    conn.execute(
        """
        INSERT INTO price_cache (coin_id, price_date, price_usd)
        VALUES (?, ?, ?)
        ON CONFLICT(coin_id, price_date) DO UPDATE SET price_usd = excluded.price_usd
        """,
        (coin_id, price_date, price_usd),
    )


def get_return(conn: sqlite3.Connection, tenure_id: int, milestone_day: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM returns WHERE tenure_id = ? AND milestone_day = ?",
        (tenure_id, milestone_day),
    ).fetchone()


def save_return(
    conn: sqlite3.Connection,
    tenure_id: int,
    milestone_day: int,
    price_at_day: float,
    return_pct: float,
    exited_before_milestone: bool,
) -> None:
    conn.execute(
        """
        INSERT INTO returns (tenure_id, milestone_day, price_at_day, return_pct, exited_before_milestone, computed_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(tenure_id, milestone_day) DO NOTHING
        """,
        (tenure_id, milestone_day, price_at_day, return_pct, int(exited_before_milestone)),
    )
