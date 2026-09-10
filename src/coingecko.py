"""CoinGecko API client with token-bucket rate limiting and retry/backoff.

Uses the free Demo API key (CG_API_KEY) when available -- registration is free
at coingecko.com/en/developers/dashboard and gives a documented, stable limit.
Without a key it falls back to the unauthenticated public endpoint, which is
throttled more aggressively and without guarantees.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import date, datetime

import requests

from src.config import get_api_key

logger = logging.getLogger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"


class CoinGeckoError(RuntimeError):
    """Raised for API failures the caller can't recover from (bad request, 404, etc)."""


class RateLimiter:
    """Token bucket: allows at most `max_calls` calls in any rolling `period` seconds."""

    def __init__(self, max_calls: int, period: float = 60.0) -> None:
        self.max_calls = max_calls
        self.period = period
        self._timestamps: deque[float] = deque()

    def wait(self) -> None:
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] > self.period:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_calls:
            sleep_for = self.period - (now - self._timestamps[0])
            if sleep_for > 0:
                logger.debug("Rate limit reached, sleeping %.1fs", sleep_for)
                time.sleep(sleep_for)
        self._timestamps.append(time.monotonic())


class CoinGeckoClient:
    def __init__(
        self,
        api_key: str | None = None,
        calls_per_minute: int | None = None,
        max_retries: int = 5,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else get_api_key()
        # Demo key: documented 30/min. No key: stay conservative -- in practice the
        # public endpoint's real shared limit is undocumented, stricter than it looks,
        # and enforced globally (not per caller), so 429s are still possible even at
        # a low local rate. The retry/backoff logic below is what actually keeps things
        # working in that case; a free Demo API key avoids the problem structurally.
        default_limit = 28 if self.api_key else 6
        self.rate_limiter = RateLimiter(calls_per_minute or default_limit)
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["x-cg-demo-api-key"] = self.api_key
        logger.info(
            "CoinGecko client ready (%s, %d req/min)",
            "with API key" if self.api_key else "no API key -- public endpoint",
            self.rate_limiter.max_calls,
        )

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{BASE_URL}{path}"
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self.rate_limiter.wait()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                backoff = min(2 ** attempt, 60)
                logger.warning("Network error on %s (attempt %d): %s -- retrying in %ds", path, attempt, exc, backoff)
                time.sleep(backoff)
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                # Exponential backoff is the floor -- some responses send a Retry-After
                # of 0 (or omit it), which would otherwise cause an immediate, useless
                # retry loop that just re-triggers the same rate limit.
                backoff = min(2 ** attempt, 60)
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        backoff = max(backoff, float(retry_after))
                    except ValueError:
                        pass
                logger.warning("Rate limited (429) on %s -- retrying in %.0fs", path, backoff)
                time.sleep(backoff)
                continue

            if resp.status_code >= 500:
                backoff = min(2 ** attempt, 60)
                logger.warning("Server error %d on %s -- retrying in %ds", resp.status_code, path, backoff)
                time.sleep(backoff)
                continue

            # 4xx other than 429: not retryable.
            raise CoinGeckoError(
                f"CoinGecko request failed: {resp.status_code} {resp.reason} for {path} -- {resp.text[:300]}"
            )

        raise CoinGeckoError(f"CoinGecko request failed after {self.max_retries} attempts for {path}: {last_error}")

    def ping(self) -> bool:
        self._get("/ping")
        return True

    def get_markets_page(self, page: int, per_page: int = 250, vs_currency: str = "usd") -> list[dict]:
        """One page of coins ordered by market cap descending."""
        return self._get(
            "/coins/markets",
            params={
                "vs_currency": vs_currency,
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
            },
        )

    def get_top_markets(self, n: int, vs_currency: str = "usd") -> list[dict]:
        """Top n coins by market cap, paginating as needed (CoinGecko caps per_page at 250)."""
        results: list[dict] = []
        page = 1
        while len(results) < n:
            per_page = min(250, n - len(results))
            batch = self.get_markets_page(page=page, per_page=per_page, vs_currency=vs_currency)
            if not batch:
                break
            results.extend(batch)
            page += 1
        return results[:n]

    def get_coins_by_ids(self, coin_ids: list[str], vs_currency: str = "usd") -> list[dict]:
        """Current market data for specific coin_ids, regardless of their current rank.
        Used to resolve symbol/name for historically-significant coins outside the
        current top-N universe (see config/seed_extra_coins.yaml)."""
        if not coin_ids:
            return []
        try:
            return self._get(
                "/coins/markets",
                params={
                    "vs_currency": vs_currency,
                    "ids": ",".join(coin_ids),
                    "per_page": len(coin_ids),
                    "sparkline": "false",
                },
            )
        except CoinGeckoError as exc:
            logger.warning("get_coins_by_ids failed for %s: %s", coin_ids, exc)
            return []

    def get_market_chart_range(
        self, coin_id: str, from_ts: int, to_ts: int, vs_currency: str = "usd"
    ) -> dict:
        """Historical prices/market_caps/volumes between unix timestamps `from_ts` and `to_ts`."""
        try:
            return self._get(
                f"/coins/{coin_id}/market_chart/range",
                params={"vs_currency": vs_currency, "from": from_ts, "to": to_ts},
            )
        except CoinGeckoError as exc:
            logger.warning("market_chart/range failed for %s: %s", coin_id, exc)
            return {"prices": [], "market_caps": [], "total_volumes": []}

    def get_global(self) -> dict | None:
        """Current global market snapshot (total market cap, BTC dominance) via /global.
        No history is available on the free tier -- this is a point-in-time read only."""
        try:
            return self._get("/global")
        except CoinGeckoError as exc:
            logger.warning("get_global failed: %s", exc)
            return None

    def get_price_on_date(self, coin_id: str, on_date: date, vs_currency: str = "usd") -> float | None:
        """Historical price on a specific date via /coins/{id}/history (dd-mm-yyyy)."""
        date_str = on_date.strftime("%d-%m-%Y")
        try:
            data = self._get(f"/coins/{coin_id}/history", params={"date": date_str, "localization": "false"})
        except CoinGeckoError as exc:
            logger.warning("history lookup failed for %s on %s: %s", coin_id, date_str, exc)
            return None
        try:
            return data["market_data"]["current_price"][vs_currency]
        except (KeyError, TypeError):
            logger.warning("No price data for %s on %s", coin_id, date_str)
            return None


def unix_ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day).timestamp())
