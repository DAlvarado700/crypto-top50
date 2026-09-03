"""Pandas-based aggregations: per-coin, per-category, and global metrics.

Stablecoins are excluded from the *global* blended return averages by default
(config.EXCLUDE_STABLECOINS_FROM_RETURNS) since their ~0%-by-design return
dilutes a headline "average top-50 return" figure. They still get their own
row in category_stats -- that number (near 0%) is itself informative.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pandas as pd

from src.config import EXCLUDE_STABLECOINS_FROM_RETURNS, MILESTONES

STABLECOIN_CATEGORY = "Stablecoin"
SURVIVAL_THRESHOLDS = (90, 180, 365)
DURATION_BUCKETS = [0, 30, 60, 90, 180, 365, float("inf")]
DURATION_BUCKET_LABELS = ["0-30", "30-60", "60-90", "90-180", "180-365", "365+"]


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
        },
        "survival_curves": _kaplan_meier_curves(tenures_view),
        "timeline": _timeline(tenures_view),
    }
