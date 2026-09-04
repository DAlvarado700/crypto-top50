# Crypto Top 50 Quant Tracker
Tracks the crypto top 50 by market cap: time spent in the ranking, post-entry returns (d20/50/100/200), and performance by category.

**Install:**
```
pip install -r requirements.txt
cp .env.example .env   # optional: free CG_API_KEY from coingecko.com/en/developers/dashboard
```
**Usage:**
```
python main.py init && python main.py backfill --years 1   # rebuilds history (~364 days, see note)
python main.py run                                          # snapshot + returns + analyze (daily use)
python main.py status                                       # terminal summary
```
Open `dashboard/index.html` by double-clicking it (no server, no internet needed), or see the live version at **[dalvarado700.github.io/crypto-top50](https://dalvarado700.github.io/crypto-top50/)**, updated daily by `.github/workflows/daily-update.yml`.

**Note:** CoinGecko's free tier caps historical data at ~365 days (even with a demo API key); `--years 3` gets clamped automatically. Run `snapshot` regularly to accumulate more history over time.

`config/categories.yaml`: editable category mapping. `config/seed_extra_coins.yaml`: force-include dead/delisted coins in the backfill (survivorship-bias mitigation, see the comment there).

Tests: `pip install pytest && pytest tests/ -v`
