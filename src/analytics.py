"""Pandas-based aggregations: per-coin, per-category, and global metrics.

Stablecoins are excluded from the *global* blended return averages by default
(config.EXCLUDE_STABLECOINS_FROM_RETURNS) since their ~0%-by-design return
dilutes a headline "average top-50 return" figure. They still get their own
row in category_stats -- that number (near 0%) is itself informative.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd

from src import btc_indicators
from src.config import EXCLUDE_STABLECOINS_FROM_RETURNS, MILESTONES

STABLECOIN_CATEGORY = "Stablecoin"
BENCHMARK_COIN_ID = "bitcoin"
SURVIVAL_THRESHOLDS = (90, 180, 365)
DURATION_BUCKETS = [0, 30, 60, 90, 180, 365, float("inf")]
DURATION_BUCKET_LABELS = ["0-30", "30-60", "60-90", "90-180", "180-365", "365+"]

ALTCOIN_SEASON_WINDOW_DAYS = 90  # matches the commonly cited "altcoin season index" methodology
ALTCOIN_SEASON_MIN_COINS = 10  # below this, the % is too noisy to report (see n alongside every average)
ALTCOIN_SEASON_LOOKBACK_TOLERANCE_DAYS = 10  # snapshot gaps mean "90 days ago" may need a nearby stand-in


def _load_frames(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    coins = pd.read_sql_query("SELECT * FROM coins", conn)
    snapshots = pd.read_sql_query("SELECT * FROM snapshots", conn)
    tenures = pd.read_sql_query("SELECT * FROM tenures", conn)
    returns = pd.read_sql_query("SELECT * FROM returns", conn)
    return {"coins": coins, "snapshots": snapshots, "tenures": tenures, "returns": returns}


def _current_duration_days(entry_date: str, exit_date: str | None, today: date) -> int:
    # pandas reads SQL NULL as NaN (float), not None/NaT -- `if exit_date` alone
    # would treat NaN as truthy and crash in date.fromisoformat(nan).
    end = date.fromisoformat(exit_date) if pd.notna(exit_date) else today
    return (end - date.fromisoformat(entry_date)).days


def _build_tenures_view(tenures: pd.DataFrame, coins: pd.DataFrame, today: date) -> pd.DataFrame:
    if tenures.empty:
        return tenures.assign(category=[], duration_days=[], is_active=[])
    t = tenures.merge(coins[["coin_id", "symbol", "name", "category"]], on="coin_id", how="left")
    t["duration_days"] = t.apply(
        lambda r: _current_duration_days(r["entry_date"], r["exit_date"], today), axis=1
    )
    t["is_active"] = t["exit_date"].isna()
    return t


def _coin_metrics(tenures_view: pd.DataFrame, snapshots: pd.DataFrame, returns: pd.DataFrame) -> list[dict]:
    if tenures_view.empty:
        return []

    latest_date = snapshots["snapshot_date"].max() if not snapshots.empty else None
    out = []

    for coin_id, group in tenures_view.groupby("coin_id"):
        first = group.iloc[0]
        coin_snapshots = snapshots[snapshots["coin_id"] == coin_id]
        current_rank = None
        if latest_date is not None:
            latest_row = coin_snapshots[coin_snapshots["snapshot_date"] == latest_date]
            if not latest_row.empty:
                current_rank = int(latest_row.iloc[0]["rank"])

        coin_returns = returns[returns["tenure_id"].isin(group["tenure_id"])]
        returns_by_milestone: dict[str, float | None] = {}
        for m in MILESTONES:
            vals = coin_returns[coin_returns["milestone_day"] == m]["return_pct"]
            returns_by_milestone[str(m)] = round(float(vals.mean()), 2) if not vals.empty else None

        out.append(
            {
                "coin_id": coin_id,
                "symbol": first["symbol"],
                "name": first["name"],
                "category": first["category"],
                "currently_in_top50": bool(group["is_active"].any()),
                "days_total_in_top50": int(group["duration_days"].sum()),
                "entries_count": int(len(group)),
                "avg_tenure_days": round(float(group["duration_days"].mean()), 1),
                "avg_rank": round(float(coin_snapshots["rank"].mean()), 1) if not coin_snapshots.empty else None,
                "best_rank": int(coin_snapshots["rank"].min()) if not coin_snapshots.empty else None,
                "current_rank": current_rank,
                "returns": returns_by_milestone,
            }
        )

    out.sort(key=lambda c: (c["current_rank"] is None, c["current_rank"] or 0))
    return out


def _composition_current(coins: pd.DataFrame, snapshots: pd.DataFrame) -> list[dict]:
    if snapshots.empty:
        return []
    latest_date = snapshots["snapshot_date"].max()
    latest = snapshots[snapshots["snapshot_date"] == latest_date].merge(coins, on="coin_id", how="left")
    total = len(latest)
    if total == 0:
        return []
    counts = latest["category"].fillna("Other").value_counts()
    return [
        {"category": cat, "count": int(n), "pct": round(100 * n / total, 1)}
        for cat, n in counts.items()
    ]


def _category_returns_stats(tenures_view: pd.DataFrame, returns: pd.DataFrame) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    if tenures_view.empty:
        return stats

    for category, group in tenures_view.groupby("category"):
        by_milestone = {}
        for m in MILESTONES:
            merged = returns[(returns["milestone_day"] == m) & (returns["tenure_id"].isin(group["tenure_id"]))]
            vals = merged["return_pct"].dropna()
            if vals.empty:
                by_milestone[str(m)] = {"mean": None, "median": None, "std": None, "n": 0}
            else:
                by_milestone[str(m)] = {
                    "mean": round(float(vals.mean()), 2),
                    "median": round(float(vals.median()), 2),
                    "std": round(float(vals.std()), 2) if len(vals) > 1 else 0.0,
                    "n": int(len(vals)),
                }
        stats[category] = by_milestone
    return stats


def _survival_rate(group: pd.DataFrame, threshold: int) -> dict:
    n = len(group)
    if n == 0:
        return {"pct": None, "n": 0}
    survived = (group["duration_days"] >= threshold).sum()
    return {"pct": round(100 * survived / n, 1), "n": int(n)}


def _category_stats(tenures_view: pd.DataFrame, returns: pd.DataFrame, composition: list[dict]) -> list[dict]:
    if tenures_view.empty:
        return []

    pct_by_cat = {c["category"]: c["pct"] for c in composition}
    returns_stats = _category_returns_stats(tenures_view, returns)

    result = []
    for category, group in tenures_view.groupby("category"):
        survival = {f"gt_{t}d": _survival_rate(group, t) for t in SURVIVAL_THRESHOLDS}
        result.append(
            {
                "category": category,
                "n_tenures": int(len(group)),
                "n_coins": int(group["coin_id"].nunique()),
                "pct_of_current_top50": pct_by_cat.get(category, 0.0),
                "avg_duration_days": round(float(group["duration_days"].mean()), 1),
                "survival": survival,
                "returns": returns_stats.get(category, {}),
            }
        )
    result.sort(key=lambda c: c["pct_of_current_top50"], reverse=True)
    return result


def _global_blended_returns(tenures_view: pd.DataFrame, returns: pd.DataFrame) -> dict:
    """Average/median return per milestone across all tenures, excluding stablecoins
    by default (see module docstring)."""
    if EXCLUDE_STABLECOINS_FROM_RETURNS:
        eligible = tenures_view[tenures_view["category"] != STABLECOIN_CATEGORY]
    else:
        eligible = tenures_view

    out = {}
    for m in MILESTONES:
        merged = returns[(returns["milestone_day"] == m) & (returns["tenure_id"].isin(eligible["tenure_id"]))]
        vals = merged["return_pct"].dropna()
        out[str(m)] = {
            "mean": round(float(vals.mean()), 2) if not vals.empty else None,
            "median": round(float(vals.median()), 2) if not vals.empty else None,
            "n": int(len(vals)),
        }
    return out


def _duration_histogram(tenures_view: pd.DataFrame) -> list[dict]:
    if tenures_view.empty:
        return []
    bucketed = pd.cut(tenures_view["duration_days"], bins=DURATION_BUCKETS, labels=DURATION_BUCKET_LABELS, right=False)
    counts = bucketed.value_counts().reindex(DURATION_BUCKET_LABELS, fill_value=0)
    return [{"bucket": label, "count": int(n)} for label, n in counts.items()]


def _monthly_churn(tenures_view: pd.DataFrame) -> list[dict]:
    if tenures_view.empty:
        return []
    entries = tenures_view.assign(month=pd.to_datetime(tenures_view["entry_date"]).dt.to_period("M").astype(str))
    entry_counts = entries.groupby("month").size()

    closed = tenures_view[tenures_view["exit_date"].notna()].copy()
    exit_counts = pd.Series(dtype=int)
    if not closed.empty:
        closed["month"] = pd.to_datetime(closed["exit_date"]).dt.to_period("M").astype(str)
        exit_counts = closed.groupby("month").size()

    months = sorted(set(entry_counts.index) | set(exit_counts.index))
    out = []
    for month in months:
        e = int(entry_counts.get(month, 0))
        x = int(exit_counts.get(month, 0))
        out.append({"month": month, "entries": e, "exits": x, "churn_rate_pct": round(100 * (e + x) / (2 * 50), 1)})
    return out


def _timeline(tenures_view: pd.DataFrame) -> list[dict]:
    if tenures_view.empty:
        return []
    events: dict[str, dict] = {}

    for _, row in tenures_view.iterrows():
        month = row["entry_date"][:7]
        events.setdefault(month, {"month": month, "entries": [], "exits": []})
        events[month]["entries"].append({"coin_id": row["coin_id"], "symbol": row["symbol"]})

        if pd.notna(row["exit_date"]):
            xmonth = row["exit_date"][:7]
            events.setdefault(xmonth, {"month": xmonth, "entries": [], "exits": []})
            events[xmonth]["exits"].append({"coin_id": row["coin_id"], "symbol": row["symbol"]})

    return [events[m] for m in sorted(events.keys())]


def _entry_rank_survival_correlation(tenures_view: pd.DataFrame) -> dict:
    closed = tenures_view[tenures_view["exit_date"].notna() & tenures_view["entry_rank"].notna()]
    if len(closed) < 3:
        return {"correlation": None, "n": int(len(closed))}
    corr = closed["entry_rank"].astype(float).corr(closed["duration_days"].astype(float))
    return {"correlation": round(float(corr), 3) if pd.notna(corr) else None, "n": int(len(closed))}


def _kaplan_meier_curves(tenures_view: pd.DataFrame) -> dict[str, list[dict]]:
    """Discrete-time Kaplan-Meier estimate of % still in top-50 vs days elapsed,
    one step function per category. Active tenures are right-censored at their
    current duration."""
    curves: dict[str, list[dict]] = {}
    if tenures_view.empty:
        return curves

    for category, group in tenures_view.groupby("category"):
        durations = group["duration_days"].astype(int).tolist()
        events = (~group["is_active"]).tolist()  # True = exited (event), False = censored

        event_times = sorted({d for d, e in zip(durations, events) if e})
        points = [{"day": 0, "survival_pct": 100.0}]
        survival = 1.0
        for t in event_times:
            n_at_risk = sum(1 for d in durations if d >= t)
            d_events = sum(1 for d, e in zip(durations, events) if d == t and e)
            if n_at_risk == 0:
                continue
            survival *= 1 - (d_events / n_at_risk)
            points.append({"day": t, "survival_pct": round(survival * 100, 1)})

        # A line dataset only draws up to its last point -- without this, the flat
        # (censored) tail after the last exit is invisible instead of extending to
        # the actual observed follow-up horizon.
        max_duration = max(durations) if durations else 0
        if max_duration > points[-1]["day"]:
            points.append({"day": max_duration, "survival_pct": points[-1]["survival_pct"]})

        curves[category] = points
    return curves


def _price_lookup(conn: sqlite3.Connection, coin_id: str) -> dict[str, float]:
    """date_str -> price for one coin, from snapshots first then price_cache.
    Uses only data already on disk -- no API calls."""
    lookup: dict[str, float] = {}
    for row in conn.execute(
        "SELECT snapshot_date AS d, price_usd AS p FROM snapshots WHERE coin_id = ? AND price_usd IS NOT NULL",
        (coin_id,),
    ):
        lookup[row["d"]] = row["p"]
    for row in conn.execute(
        "SELECT price_date AS d, price_usd AS p FROM price_cache WHERE coin_id = ? AND price_usd IS NOT NULL",
        (coin_id,),
    ):
        lookup.setdefault(row["d"], row["p"])
    return lookup


def _btc_benchmark(tenures_view: pd.DataFrame, returns: pd.DataFrame, conn: sqlite3.Connection) -> dict:
    """Compares 'buy every new top-50 entrant and hold' against 'buy BTC on that same
    date and hold the same number of days' -- identical entry dates and windows, only
    the asset differs. Reuses cached prices only; adds no new API calls.

    Always returns one entry per milestone (n=0 with null values when there's nothing
    to compare), matching the shape of _global_blended_returns / _category_returns_stats
    rather than short-circuiting to `{}` on empty input.
    """
    btc_prices = _price_lookup(conn, BENCHMARK_COIN_ID) if not tenures_view.empty else {}

    eligible = tenures_view[tenures_view["coin_id"] != BENCHMARK_COIN_ID]
    if EXCLUDE_STABLECOINS_FROM_RETURNS:
        eligible = eligible[eligible["category"] != STABLECOIN_CATEGORY]
    entry_date_by_tenure = eligible.set_index("tenure_id")["entry_date"] if not eligible.empty else pd.Series(dtype=object)

    out = {}
    for m in MILESTONES:
        strat_vals: list[float] = []
        btc_vals: list[float] = []
        merged = (
            returns[(returns["milestone_day"] == m) & (returns["tenure_id"].isin(eligible["tenure_id"]))]
            if not eligible.empty
            else returns.iloc[0:0]
        )
        for _, row in merged.iterrows():
            if pd.isna(row["return_pct"]):
                continue
            entry_date = entry_date_by_tenure.get(row["tenure_id"])
            if entry_date is None:
                continue
            target_date = (date.fromisoformat(entry_date) + timedelta(days=int(m))).isoformat()
            btc_entry = btc_prices.get(entry_date)
            btc_target = btc_prices.get(target_date)
            if btc_entry is None or btc_target is None:
                continue
            strat_vals.append(float(row["return_pct"]))
            btc_vals.append(((btc_target - btc_entry) / btc_entry) * 100)

        if not strat_vals:
            out[str(m)] = {"avg_strategy_return": None, "avg_btc_return": None, "alpha": None, "n": 0}
            continue
        avg_strat = sum(strat_vals) / len(strat_vals)
        avg_btc = sum(btc_vals) / len(btc_vals)
        out[str(m)] = {
            "avg_strategy_return": round(avg_strat, 2),
            "avg_btc_return": round(avg_btc, 2),
            "alpha": round(avg_strat - avg_btc, 2),
            "n": len(strat_vals),
        }
    return out


def _build_price_index(snapshots: pd.DataFrame) -> dict[str, pd.Series]:
    """One date-indexed, sorted price Series per coin_id, built from `snapshots`
    (no new API calls -- this is the same daily price already collected for the
    top-50 tenure tracking, just reshaped for per-coin lookback)."""
    out: dict[str, pd.Series] = {}
    if snapshots.empty:
        return out
    priced = snapshots.dropna(subset=["price_usd"])
    for coin_id, g in priced.groupby("coin_id"):
        s = g.set_index(pd.to_datetime(g["snapshot_date"]))["price_usd"].sort_index()
        out[coin_id] = s[~s.index.duplicated(keep="last")]
    return out


def _nearest_price_on_or_before(series: pd.Series, target: pd.Timestamp, tolerance_days: int) -> float | None:
    pos = series.index.searchsorted(target, side="right") - 1
    if pos < 0:
        return None
    found_date = series.index[pos]
    if (target - found_date).days > tolerance_days:
        return None
    return float(series.iloc[pos])


def _altcoin_season_index(snapshots: pd.DataFrame, coins: pd.DataFrame) -> dict:
    """For each day, the % of that day's top-50 coins (excluding BTC and
    stablecoins) whose trailing ALTCOIN_SEASON_WINDOW_DAYS return beat BTC's
    return over the same window -- the standard "altcoin season index"
    methodology, computed here entirely from already-collected snapshot prices
    (no new fetching). >=75% is conventionally "altcoin season", <=25% is
    "bitcoin season"; the dashboard applies that labeling, not this function."""
    empty = {"history": [], "current": None, "window_days": ALTCOIN_SEASON_WINDOW_DAYS, "min_coins": ALTCOIN_SEASON_MIN_COINS}
    if snapshots.empty:
        return empty

    price_by_coin = _build_price_index(snapshots)
    btc_series = price_by_coin.get(BENCHMARK_COIN_ID)
    if btc_series is None:
        return empty

    stable_ids = set(coins.loc[coins["category"] == STABLECOIN_CATEGORY, "coin_id"])
    window = pd.Timedelta(days=ALTCOIN_SEASON_WINDOW_DAYS)
    tol = ALTCOIN_SEASON_LOOKBACK_TOLERANCE_DAYS

    history = []
    for snapshot_date, day_rows in snapshots.groupby("snapshot_date"):
        d = pd.Timestamp(snapshot_date)
        past = d - window

        btc_now = _nearest_price_on_or_before(btc_series, d, 0)
        btc_past = _nearest_price_on_or_before(btc_series, past, tol)
        if not btc_now or not btc_past:
            history.append({"date": snapshot_date, "pct_beating_btc": None, "n": 0})
            continue
        btc_return = (btc_now - btc_past) / btc_past

        eligible = 0
        beating = 0
        for coin_id in day_rows["coin_id"]:
            if coin_id == BENCHMARK_COIN_ID or coin_id in stable_ids:
                continue
            series = price_by_coin.get(coin_id)
            if series is None:
                continue
            now_price = _nearest_price_on_or_before(series, d, 0)
            past_price = _nearest_price_on_or_before(series, past, tol)
            if not now_price or not past_price:
                continue
            eligible += 1
            if (now_price - past_price) / past_price > btc_return:
                beating += 1

        pct = round(beating / eligible * 100, 1) if eligible >= ALTCOIN_SEASON_MIN_COINS else None
        history.append({"date": snapshot_date, "pct_beating_btc": pct, "n": eligible})

    return {
        "history": history,
        "current": history[-1] if history else None,
        "window_days": ALTCOIN_SEASON_WINDOW_DAYS,
        "min_coins": ALTCOIN_SEASON_MIN_COINS,
    }


def _hall_of_fame(conn: sqlite3.Connection, top_n: int = 5) -> dict:
    """The single best and worst (tenure, milestone) returns ever recorded -- real
    individual outcomes, not category averages that smooth them out."""
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.coin_id, c.symbol, c.name, c.category,
                   t.entry_date, r.milestone_day, r.return_pct
            FROM returns r
            JOIN tenures t ON t.tenure_id = r.tenure_id
            JOIN coins c ON c.coin_id = t.coin_id
            WHERE r.return_pct IS NOT NULL
            """
        )
    ]
    if not rows:
        return {"winners": [], "losers": []}

    rows.sort(key=lambda e: e["return_pct"], reverse=True)
    winners = rows[:top_n]
    losers = list(reversed(rows[-top_n:]))

    def _fmt(e: dict) -> dict:
        return {
            "coin_id": e["coin_id"],
            "symbol": e["symbol"],
            "name": e["name"],
            "category": e["category"],
            "entry_date": e["entry_date"],
            "milestone_day": e["milestone_day"],
            "return_pct": round(e["return_pct"], 2),
        }

    return {"winners": [_fmt(e) for e in winners], "losers": [_fmt(e) for e in losers]}


def _coin_series(conn: sqlite3.Connection, frames: dict, tenures_view: pd.DataFrame) -> dict:
    """Per-coin price history and full tenure list, for the dashboard's coin detail
    drill-down. Price history is whatever we already have cached (snapshots +
    price_cache) -- no new API calls."""
    out: dict[str, dict] = {}
    for coin_id in frames["coins"]["coin_id"]:
        series = [{"date": d, "price": p} for d, p in sorted(_price_lookup(conn, coin_id).items())]

        tenure_list = []
        for _, t in tenures_view[tenures_view["coin_id"] == coin_id].iterrows():
            tenure_list.append(
                {
                    "tenure_id": int(t["tenure_id"]),
                    "entry_date": t["entry_date"],
                    "entry_price": t["entry_price"],
                    "exit_date": t["exit_date"] if pd.notna(t["exit_date"]) else None,
                    "exit_price": t["exit_price"] if pd.notna(t["exit_price"]) else None,
                    "duration_days": int(t["duration_days"]),
                    "is_active": bool(t["is_active"]),
                }
            )
        out[coin_id] = {"price_series": series, "tenures": tenure_list}
    return out


def build_analysis(conn: sqlite3.Connection) -> dict:
    """Builds the full analysis dict written to output/analysis.json."""
    frames = _load_frames(conn)
    today = datetime.now().date()
    tenures_view = _build_tenures_view(frames["tenures"], frames["coins"], today)

    composition = _composition_current(frames["coins"], frames["snapshots"])
    category_stats = _category_stats(tenures_view, frames["returns"], composition)
    coins_metrics = _coin_metrics(tenures_view, frames["snapshots"], frames["returns"])

    snapshot_dates = frames["snapshots"]["snapshot_date"]
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_days": int(snapshot_dates.nunique()) if not snapshot_dates.empty else 0,
        "first_snapshot_date": snapshot_dates.min() if not snapshot_dates.empty else None,
        "last_snapshot_date": snapshot_dates.max() if not snapshot_dates.empty else None,
        "coins_tracked": int(frames["coins"]["coin_id"].nunique()),
        "tenures_total": int(len(frames["tenures"])),
        "tenures_active": int(tenures_view["is_active"].sum()) if not tenures_view.empty else 0,
        "tenures_closed": int((~tenures_view["is_active"]).sum()) if not tenures_view.empty else 0,
        "returns_computed": int(len(frames["returns"])),
        "returns_expected": int(len(frames["tenures"]) * len(MILESTONES)),
        "exclude_stablecoins_from_global_returns": EXCLUDE_STABLECOINS_FROM_RETURNS,
    }

    return {
        "meta": meta,
        "composition_current": composition,
        "category_stats": category_stats,
        "coins": coins_metrics,
        "global": {
            "blended_returns": _global_blended_returns(tenures_view, frames["returns"]),
            "duration_histogram": _duration_histogram(tenures_view),
            "monthly_churn": _monthly_churn(tenures_view),
            "entry_rank_survival_correlation": _entry_rank_survival_correlation(tenures_view),
            "benchmark_vs_btc": _btc_benchmark(tenures_view, frames["returns"], conn),
        },
        "survival_curves": _kaplan_meier_curves(tenures_view),
        "timeline": _timeline(tenures_view),
        "hall_of_fame": _hall_of_fame(conn),
        "coin_series": _coin_series(conn, frames, tenures_view),
        "btc_indicators": btc_indicators.compute(conn),
        "altcoin_season_index": _altcoin_season_index(frames["snapshots"], frames["coins"]),
    }
