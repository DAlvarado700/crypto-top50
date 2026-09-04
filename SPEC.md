# SPEC: Crypto Top 50 Quantitative Tracker

## 1. Goal

A Python system that tracks the **top 50 cryptocurrencies by market cap** and generates purely quantitative analysis on:

- **Tenure**: how long each coin has spent in the top 50, how many times it has entered and exited, average duration per stay
- **Post-entry returns**: % return at 20, 50, 100, and 200 days after a coin enters the top 50
- **Category composition**: what % of the top 50 is Layer 1, Layer 2, DeFi, Stablecoin, Meme, AI, etc.
- **Category performance**: average return and average tenure grouped by coin type

The project's thesis: *entering the top 50 is a measurable signal. What happens next? Which categories survive, and which are flashes in the pan?*

---

## 2. Stack

| Component | Technology | Why |
|---|---|---|
| Language | Python 3.11+ | data ecosystem |
| Market data | CoinGecko API (free tier) | no API key required, history back to 2013 |
| Storage | SQLite | persistent history, SQL queries, single file |
| Analysis | pandas | category aggregations |
| Config | YAML | editable categories without touching code |
| CLI | `argparse` | clear commands, no extra dependency |
| Dashboard | Static HTML + Chart.js | opens without a server, easy to share |

**Constraint:** CoinGecko's free tier is rate-limited (~30 requests/minute). The client handles this with automatic rate limiting and backoff retries.

---

## 3. File structure

```
crypto-top50/
├── SPEC.md
├── README.md
├── requirements.txt
├── config/
│   ├── categories.yaml       # coin_id -> category mapping
│   └── seed_extra_coins.yaml # force-included dead/delisted coins (survivorship-bias mitigation)
├── src/
│   ├── db.py                 # SQLite schema + queries
│   ├── coingecko.py          # API client with rate limiting
│   ├── tracker.py            # top-50 entry/exit logic
│   ├── returns.py            # d20/d50/d100/d200 return calculation
│   ├── analytics.py          # category aggregations (pandas)
│   └── export.py             # generates JSON/CSV for the dashboard
├── dashboard/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── data/
│   └── tracker.db            # SQLite, tracked in git as an audit trail
├── output/
│   ├── analysis.json
│   └── returns.csv
├── tests/
└── main.py                   # CLI entrypoint
```

---

## 4. Database schema

```sql
-- Coin catalog
CREATE TABLE coins (
    coin_id      TEXT PRIMARY KEY,
    symbol       TEXT NOT NULL,
    name         TEXT NOT NULL,
    category     TEXT,
    first_seen   DATE
);

-- Daily top-50 snapshot
CREATE TABLE snapshots (
    snapshot_date DATE NOT NULL,
    coin_id       TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    price_usd     REAL,
    market_cap    REAL,
    volume_24h    REAL,
    PRIMARY KEY (snapshot_date, coin_id),
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- Tenure periods (one row per "stay" in the top 50)
CREATE TABLE tenures (
    tenure_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    coin_id       TEXT NOT NULL,
    entry_date    DATE NOT NULL,
    entry_price   REAL NOT NULL,
    entry_rank    INTEGER,
    exit_date     DATE,              -- NULL if still in
    exit_price    REAL,
    days_in_top50 INTEGER,
    FOREIGN KEY (coin_id) REFERENCES coins(coin_id)
);

-- Returns computed per milestone
CREATE TABLE returns (
    tenure_id     INTEGER NOT NULL,
    milestone_day INTEGER NOT NULL,  -- 20, 50, 100, 200
    price_at_day  REAL,
    return_pct    REAL,
    computed_at   TIMESTAMP,
    PRIMARY KEY (tenure_id, milestone_day),
    FOREIGN KEY (tenure_id) REFERENCES tenures(tenure_id)
);

-- Historical price cache (avoids re-calling the API)
CREATE TABLE price_cache (
    coin_id    TEXT NOT NULL,
    price_date DATE NOT NULL,
    price_usd  REAL,
    PRIMARY KEY (coin_id, price_date)
);
```

**Indexes:** `snapshots(coin_id)`, `tenures(coin_id, exit_date)`, `price_cache(coin_id)`.

---

## 5. Core logic: entry/exit detection

Every time a snapshot runs:

```
top50_today     = set of coin_ids in today's top 50
top50_yesterday = set of coin_ids from the last snapshot

ENTRIES = top50_today - top50_yesterday
    -> create a new row in `tenures` with entry_date = today, entry_price = current price

EXITS = top50_yesterday - top50_today
    -> close the open tenure: exit_date = today, compute days_in_top50

CONTINUING = top50_today ∩ top50_yesterday
    -> just update days_in_top50 on the open tenure
```

**Gap handling:** if snapshots aren't run for a stretch (e.g. the daily job missed a run), continuity is not assumed — the gap is logged and affected tenures are flagged with `has_gap`.

---

## 6. Historical backfill

Two-mode approach:

- **Mode A — Synthetic backfill:** uses CoinGecko's `/coins/{id}/market_chart/range` endpoint to reconstruct historical market cap for the ~150 largest coins, then recomputes the top 50 day by day going backward. Generates a full history in one shot (~150 API calls). This is the `backfill` command. Free-tier CoinGecko caps this at ~365 days even with a demo API key.
- **Mode B — Cumulative:** the daily `snapshot`/`run` command, accumulating real history going forward, unbounded by the backfill cap.

---

## 7. Return calculation

For each `tenure`, for each milestone in `[20, 50, 100, 200]`:

```python
target_date = entry_date + timedelta(days=milestone)

if target_date > today:
    skip  # that much time hasn't passed yet

price_at_day = get_price(coin_id, target_date)   # with cache
return_pct = ((price_at_day - entry_price) / entry_price) * 100
```

**Rules:**
- `price_cache` is always checked before calling the API
- Already-computed returns are immutable, never recalculated
- If a tenure ended before the milestone (e.g. exited on day 30, milestone d50), the return is still computed and flagged `exited_before_milestone` — it's still valuable information

---

## 8. Categorization

`config/categories.yaml` maps each `coin_id` to a category (Layer 1, Layer 2, DeFi, Oracle / Infra, Stablecoin, Meme, Exchange Token, AI / DePIN, Payments / RWA, Privacy, Gaming / Metaverse), with an `"Other"` fallback.

Any coin without an explicit category is assigned the fallback and logged with a warning, never silently dropped.

`config/seed_extra_coins.yaml` force-includes coins that have since been delisted/died, to mitigate survivorship bias in the backfill.

---

## 9. CLI

```bash
python main.py init                      # creates DB + schema
python main.py backfill --years 3        # rebuilds history (clamped by CoinGecko's free-tier limit)
python main.py snapshot                  # captures today's top 50
python main.py returns                   # computes pending returns
python main.py analyze                   # generates output/analysis.json
python main.py export --format csv       # exports the table
python main.py run                       # snapshot + returns + analyze
python main.py status                    # terminal summary
```

---

## 10. Metrics `analytics.py` computes

**Per coin:** total days in the top 50, number of entries, average duration per stay, best/current rank, d20/d50/d100/d200 returns per tenure, still-in status.

**Per category:** % of current top 50, average and median return per milestone (median matters — a single outlier meme coin distorts the average), average duration, survival rate (>90/180/365 days), return volatility (std dev). BTC benchmark: average alpha vs. just holding BTC over the same window.

**Global:** monthly top-50 turnover rate, duration distribution, correlation between entry rank and survival.

Every average is reported alongside `n` (sample size) — a category with 3 coins isn't comparable to one with 16.

---

## 11. Dashboard

Static HTML that loads `dashboard/data.js`. No build step, no framework, no server — opens with a double-click, and is deployed to GitHub Pages by the daily CI workflow.

**Views:**
1. **Overview** — KPIs, current composition, average return by category
2. **Composition** — breakdown by category with each category's coins
3. **Returns** — category × milestone heatmap (d20/d50/d100/d200), mean and median
4. **Survival** — Kaplan-Meier curve: % still in the top 50 vs. days elapsed, one line per category
5. **Coins** — filterable, sortable table with all metrics, click-through to per-coin price history and tenure detail
6. **Timeline** — monthly timeline of entries and exits

Dark, monospace, financial-terminal aesthetic.

---

## 12. Known design tradeoffs

- **Survivorship bias in the backfill:** reconstructing the top 50 from today's largest coins misses coins that collapsed and dropped out entirely before the backfill window. `seed_extra_coins.yaml` is a manual mitigation, not a full fix.
- **Rebrandings/migrations** (e.g. MATIC → POL, LUNA → LUNC) are treated as distinct `coin_id`s unless manually mapped.
- **Stablecoins** are included in return averages as-is (~0% by design), which drags category-level averages down when compared side-by-side with volatile categories — the per-category breakdown makes this visible rather than hiding it.
- Daily price is stored only via `price_cache` as milestones are computed, not for every tracked coin every day.
