"""
WalletIntel v2 — Token Metadata Resolver

Fetches token names/symbols from DexScreener API.
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

    Uses DexScreener API (free, no key needed).
    Results cached permanently in memory.
    """

    DEXSCREENER_URL = "https://api.dexscreener.com/tokens/v1/solana/{mint}"

    # Well-known tokens (hardcoded, no API needed)
    KNOWN_TOKENS = {
        "So11111111111111111111111111111111111111112": ("SOL", "Wrapped SOL"),
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", "USD Coin"),
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", "Tether USD"),
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": ("BONK", "Bonk"),
        "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": ("WIF", "dogwifhat"),
        "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": ("JUP", "Jupiter"),
        "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr": ("POPCAT", "Popcat"),
    }

    def __init__(self):
        self._cache: Dict[str, Tuple[str, str]] = dict(self.KNOWN_TOKENS)
        self._client: Optional[httpx.AsyncClient] = None
        self._failed: set = set()

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
        """Resolve a single mint to (symbol, name)."""
        if mint in self._cache:
            return self._cache[mint]
        if mint in self._failed:
            return ("", "")

        result = await self._fetch_dexscreener(mint)
        if result:
            self._cache[mint] = result
            return result

        self._failed.add(mint)
        return ("", "")

    async def resolve_batch(self, mints: List[str]) -> Dict[str, Tuple[str, str]]:
        """Resolve multiple mints concurrently."""
        results = {}
        to_fetch = []

        for mint in mints:
            if mint in self._cache:
                results[mint] = self._cache[mint]
            elif mint not in self._failed:
                to_fetch.append(mint)

        if not to_fetch:
            return results

        logger.info(f"Resolving {len(to_fetch)} token symbols via DexScreener...")

        sem = asyncio.Semaphore(3)  # DexScreener rate limit is tight

        async def fetch_one(m: str):
            async with sem:
                r = await self._fetch_dexscreener(m)
                if r:
                    self._cache[m] = r
                    results[m] = r
                else:
                    self._failed.add(m)
                await asyncio.sleep(0.35)  # ~3 req/sec

        tasks = [fetch_one(m) for m in to_fetch]
        await asyncio.gather(*tasks)

        found = len([m for m in to_fetch if m in results])
        logger.info(f"Resolved {found}/{len(to_fetch)} token symbols")

        return results

    async def _fetch_dexscreener(self, mint: str) -> Optional[Tuple[str, str]]:
        """Fetch token info from DexScreener API."""
        if not self._client:
            return None

        try:
            url = self.DEXSCREENER_URL.format(mint=mint)
            resp = await self._client.get(url)

            if resp.status_code == 200:
                data = resp.json()
                # DexScreener returns array of pairs
                if isinstance(data, list) and len(data) > 0:
                    pair = data[0]
                    base = pair.get("baseToken", {})
                    quote = pair.get("quoteToken", {})

                    # Check if our mint is base or quote
                    if base.get("address") == mint:
                        symbol = base.get("symbol", "")
                        name = base.get("name", "")
                    elif quote.get("address") == mint:
                        symbol = quote.get("symbol", "")
                        name = quote.get("name", "")
                    else:
                        symbol = base.get("symbol", "")
                        name = base.get("name", "")

                    if symbol:
                        return (symbol, name)

            return None

        except Exception as e:
            logger.debug(f"DexScreener lookup failed for {mint[:12]}...: {e}")
            return None

    def cache_stats(self) -> Dict:
        return {
            "cached_tokens": len(self._cache),
            "failed_lookups": len(self._failed),
        }
