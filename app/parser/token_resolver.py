"""
WalletIntel v2 — Token Metadata Resolver

Fetches token names/symbols from Jupiter Token API.
Caches permanently (token metadata doesn't change).
"""
import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


class TokenResolver:
    """
    Resolve token mint addresses to name/symbol.

    Uses Jupiter Token API (free, no key needed).
    Results cached permanently in memory.
    """

    JUPITER_URL = "https://tokens.jup.ag/token/{mint}"

    # Well-known tokens (hardcoded, no API needed)
    KNOWN_TOKENS = {
        "So11111111111111111111111111111111111111112": ("SOL", "Wrapped SOL"),
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", "USD Coin"),
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", "Tether USD"),
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": ("BONK", "Bonk"),
        "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": ("WIF", "dogwifhat"),
        "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": ("JUP", "Jupiter"),
        "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr": ("POPCAT", "Popcat"),
        "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof": ("RENDER", "Render Token"),
    }

    def __init__(self):
        self._cache: Dict[str, Tuple[str, str]] = dict(self.KNOWN_TOKENS)
        self._client: Optional[httpx.AsyncClient] = None
        self._failed: set = set()  # mints that failed lookup, don't retry

    async def start(self):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=10),
        )

    async def stop(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def get_cached(self, mint: str) -> Optional[Tuple[str, str]]:
        """Get symbol/name from cache. Returns (symbol, name) or None."""
        return self._cache.get(mint)

    async def resolve(self, mint: str) -> Tuple[str, str]:
        """
        Resolve a single mint to (symbol, name).
        Returns ("", "") if not found.
        """
        # Check cache
        if mint in self._cache:
            return self._cache[mint]

        # Skip known failures
        if mint in self._failed:
            return ("", "")

        # Query Jupiter
        result = await self._fetch_jupiter(mint)
        if result:
            self._cache[mint] = result
            return result

        # Mark as failed
        self._failed.add(mint)
        return ("", "")

    async def resolve_batch(self, mints: List[str]) -> Dict[str, Tuple[str, str]]:
        """
        Resolve multiple mints concurrently.
        Returns {mint: (symbol, name)} for all found tokens.
        """
        results = {}
        to_fetch = []

        for mint in mints:
            if mint in self._cache:
                results[mint] = self._cache[mint]
            elif mint not in self._failed:
                to_fetch.append(mint)

        if not to_fetch:
            return results

        logger.info(f"Resolving {len(to_fetch)} token symbols...")

        # Fetch concurrently with semaphore
        sem = asyncio.Semaphore(5)

        async def fetch_one(m: str):
            async with sem:
                r = await self._fetch_jupiter(m)
                if r:
                    self._cache[m] = r
                    results[m] = r
                else:
                    self._failed.add(m)
                await asyncio.sleep(0.1)  # rate limit

        tasks = [fetch_one(m) for m in to_fetch]
        await asyncio.gather(*tasks)

        found = len([m for m in to_fetch if m in results])
        logger.info(f"Resolved {found}/{len(to_fetch)} token symbols")

        return results

    async def _fetch_jupiter(self, mint: str) -> Optional[Tuple[str, str]]:
        """Fetch token info from Jupiter API."""
        if not self._client:
            return None

        try:
            url = self.JUPITER_URL.format(mint=mint)
            resp = await self._client.get(url)

            if resp.status_code == 200:
                data = resp.json()
                symbol = data.get("symbol", "")
                name = data.get("name", "")
                if symbol:
                    return (symbol, name)

            return None

        except Exception as e:
            logger.debug(f"Jupiter lookup failed for {mint[:12]}...: {e}")
            return None

    def cache_stats(self) -> Dict:
        return {
            "cached_tokens": len(self._cache),
            "failed_lookups": len(self._failed),
        }
