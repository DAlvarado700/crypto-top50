# Prompt for Claude Code — Crypto Top 50 Quant Tracker

> **How to use this file:**
> 1. Create the project folder: `mkdir crypto-top50 && cd crypto-top50`
> 2. Save this file inside it as `SPEC.md`
> 3. Open Claude Code in that folder: `claude`
> 4. Paste the "INITIAL PROMPT" block below
> 5. Then ask for the phases one at a time

---

## INITIAL PROMPT (paste this as-is)

```
Read SPEC.md in this folder. It's the full specification of what I want to build.

Before writing any code:
1. Confirm you understood the goal in 3-4 bullets
2. Propose the file structure you're going to create
3. Tell me which technical decisions you'd make differently, and why

Don't write code yet. Wait for my OK.
```

---

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
| Market data | CoinGecko API (free tier) | no API key, history back to 2013 |
| Storage | SQLite | persistent history, SQL queries, single file |
| Analysis | pandas | category aggregations |
| Config | `.yaml` or `.toml` file | editable categories without touching code |
| CLI | `argparse` or `typer` | clear commands |
| Dashboard | Static HTML + Chart.js | opens without a server, easy to share |

**Important constraint:** CoinGecko's free tier allows ~30 requests/minute. The system must respect this with automatic rate limiting and backoff retries.

---

## 3. Target file structure

```
crypto-top50/
├── SPEC.md
├── README.md
├── requirements.txt
├── config/
│   └── categories.yaml       # coin_id -> category mapping
├── src/
│   ├── __init__.py
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
│   └── tracker.db            # SQLite (gitignored)
├── output/
│   ├── analysis.json
│   └── returns.csv
├── tests/
│   └── test_returns.py
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

**Suggested indexes:** `snapshots(coin_id)`, `tenures(coin_id, exit_date)`, `price_cache(coin_id)`.

---

## 5. Core logic: entry/exit detection

This is the heart of the system. Every time a snapshot runs:

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

**Edge case to handle:** if there's a gap in the snapshots (the script didn't run for a week), don't assume continuity. Log the gap and flag the affected tenures with a `has_gap` flag.

---

## 6. Historical backfill

The problem: if I start today, I have no history. Two-mode solution:

### Mode A — Synthetic backfill (recommended to get started)
Use CoinGecko's `/coins/{id}/market_chart/range` endpoint to reconstruct historical market cap for the ~150 largest coins, and **recompute the top 50 day by day going backward** over the last 2-3 years.

This generates years of real tenures and returns in one shot, with no waiting.

**Cost:** ~150 API calls (one per coin, each returning the full range). Very reasonable.

### Mode B — Cumulative
Run the daily snapshot and let history accumulate. Complements Mode A going forward.

**Implement Mode A as the `backfill` command.** It's the difference between a project with data and an empty one.

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

**Requirements:**
- Always check `price_cache` before calling the API
- Already-computed returns are never recalculated (they're immutable)
- If a tenure ended before the milestone (e.g. exited the top 50 on day 30, milestone d50), **still compute the return** — it's valuable information. Flag it with `exited_before_milestone`.

---

## 8. Categorization

`config/categories.yaml` file:

```yaml
categories:
  Layer 1:
    - bitcoin
    - ethereum
    - solana
    - cardano
    - avalanche-2
    - tron
    - polkadot
    - near
    - aptos
    - sui
    - the-open-network
    - internet-computer
    - hedera-hashgraph
    - cosmos
    - algorand
    - kaspa

  Layer 2:
    - matic-network
    - arbitrum
    - optimism
    - immutable-x
    - mantle
    - starknet

  DeFi:
    - uniswap
    - aave
    - maker
    - lido-dao
    - jupiter-exchange-solana
    - ethena

  Oracle / Infra:
    - chainlink
    - the-graph
    - filecoin

  Stablecoin:
    - tether
    - usd-coin
    - dai
    - first-digital-usd
    - ethena-usde

  Meme:
    - dogecoin
    - shiba-inu
    - pepe
    - bonk
    - dogwifcoin
    - floki

  Exchange Token:
    - binancecoin
    - okb
    - crypto-com-chain
    - leo-token

  AI / DePIN:
    - render-token
    - bittensor
    - fetch-ai
    - filecoin
    - helium

  Payments / RWA:
    - ripple
    - stellar
    - ondo-finance

  Privacy:
    - monero
    - zcash

  Gaming / Metaverse:
    - the-sandbox
    - decentraland
    - immutable-x
    - gala

fallback: "Other"
```

**Requirement:** when running the tracker, if a coin shows up without a category, print a clear warning listing it so I can add it manually. Never fail silently.

---

## 9. CLI

```bash
python main.py init                      # creates DB + schema
python main.py backfill --years 3        # rebuilds history
python main.py snapshot                  # captures today's top 50
python main.py returns                   # computes pending returns
python main.py analyze                   # generates output/analysis.json
python main.py export --format csv       # exports the table
python main.py run                       # snapshot + returns + analyze
python main.py status                    # terminal summary
```

The `status` command should print something like this to the terminal:

```
┌─ TOP 50 CRYPTO TRACKER ─────────────────────────┐
│ Snapshots:        847 days (2023-01-15 -> today)│
│ Coins tracked:      94                          │
│ Total tenures:     142 (50 active, 92 closed)   │
│ Returns computed:   388 / 424                   │
├─────────────────────────────────────────────────┤
│ CURRENT COMPOSITION                              │
│   Layer 1        32%  ████████████              │
│   DeFi           16%  ██████                    │
│   Stablecoin     12%  ████                      │
│   Meme           10%  ███                       │
├─────────────────────────────────────────────────┤
│ AVG d90 RETURN BY CATEGORY                      │
│   AI / DePIN    +142%                           │
│   Meme           +87%                           │
│   Layer 1        +31%                           │
│   Stablecoin      +0.1%                         │
└─────────────────────────────────────────────────┘
```

---

## 10. Metrics `analytics.py` must compute

### Per coin
- Total days in the top 50 (sum across all tenures)
- Number of entries (how many times it has entered)
- Average duration per stay
- Average rank, minimum (best) rank, current rank
- d20/d50/d100/d200 returns for each tenure
- Still in? yes/no

### Per category
- % of the current top 50
- Average and **median** return at each milestone (the median matters: averages get distorted by a single +3000% meme coin)
- Average duration in the top 50
- Survival rate: % of coins in that category surviving >90, >180, >365 days
- Return volatility (standard deviation)

### Global
- Monthly top-50 turnover (churn) rate
- Duration distribution (histogram)
- Correlation between entry rank and survival
- Does entering at rank 45 vs. rank 30 predict anything?

**Important statistical note:** always report `n` (sample size) alongside every average. A category with 3 coins isn't comparable to one with 16.

---

## 11. Dashboard

Static HTML that loads `output/analysis.json`. No build step, no framework, no server — opens with a double-click.

**Views:**
1. **Overview** — KPIs, current composition, average return by category
2. **Composition** — breakdown by category with each category's coins
3. **Returns** — category × milestone heatmap (d20/d50/d100/d200), with mean and median
4. **Survival** — Kaplan-Meier-style curve: % still in the top 50 vs. days elapsed, one line per category
5. **Coins** — filterable, sortable table with all metrics
6. **Timeline** — monthly timeline of entries and exits

**Design:** dark, monospace typography for numbers, financial-terminal aesthetic. It needs to look good in a screenshot because I'm going to publish it.

---

## 12. Acceptance criteria

- [ ] `python main.py init && python main.py backfill --years 2` runs without errors
- [ ] The DB contains >500 days of snapshots after the backfill
- [ ] `analyze` produces a valid JSON with all the metrics from section 10
- [ ] The dashboard opens in the browser and renders every view
- [ ] Rate limiting works: no 429 errors during a full backfill
- [ ] If I delete `data/tracker.db` and run `init` + `backfill`, I get the same result (reproducible)
- [ ] There are tests for return calculation and entry/exit detection
- [ ] `README.md` explains installation and usage in under 20 lines

---

## 13. Build phases

Ask Claude Code for these one at a time, verifying each before moving on:

**Phase 1 — Foundation**
`db.py` (schema + init), `coingecko.py` (client with rate limiting), `init` command, `requirements.txt`.
*Verify:* the DB gets created, a test call to CoinGecko works.

**Phase 2 — Snapshots**
`tracker.py` with entry/exit logic, `snapshot` command, `categories.yaml`.
*Verify:* run `snapshot` twice, confirm it doesn't duplicate tenures.

**Phase 3 — Backfill**
Historical reconstruction of the top 50 going backward.
*Verify:* `status` shows hundreds of days of history.

**Phase 4 — Returns**
`returns.py` with price caching, `returns` command.
*Verify:* manually spot-check one return against the CoinGecko website.

**Phase 5 — Analytics**
`analytics.py` with pandas, `analyze` command, `status` with nice terminal output.
*Verify:* the JSON has every metric.

**Phase 6 — Dashboard**
HTML/CSS/JS.
*Verify:* opens in a browser, every view renders.

**Phase 7 — Polish**
Tests, README, `.gitignore`, error handling, logging.

---

## 14. Code style preferences

- Type hints on every function
- Short docstrings, in English
- No unnecessary dependencies — `requests`, `pandas`, `pyyaml` is enough
- API errors should fail with clear messages, not raw stack traces
- Logging via the `logging` module, not scattered `print()` calls
- Small, testable functions with no hidden side effects
- No over-engineering: no abstract classes or design patterns where a plain function will do

---

## 15. Open questions to discuss with Claude Code

Before Phase 3, raise these with it:

1. Should the backfill reconstruct the top 50 from today's top 150, or is there a way to get the real historical ranking? (Survivorship bias: coins that collapsed and dropped out of the top 150 wouldn't show up.)
2. How should rebrandings and token migrations be handled? (MATIC -> POL, LUNA -> LUNC)
3. Should stablecoins be excluded from return averages? Their return is ~0% by design and drags the averages down.
4. Is it worth storing the daily price of every tracked coin, or only at the milestones?
