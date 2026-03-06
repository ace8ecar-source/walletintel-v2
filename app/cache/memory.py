"""
WalletIntel v2 — Cache Layer

Simple in-memory cache with TTL for MVP.
Can be swapped for Redis/PostgreSQL later without changing interface.
"""
import time
import logging
import json
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

from app.analytics.engine import WalletAnalytics

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    data: Dict
    created_at: float
    expires_at: float
    hits: int = 0


class WalletCache:
    """
    In-memory wallet analytics cache.

    Features:
    - TTL-based expiration
    - LRU eviction when max size reached
    - Stats tracking
    """

    def __init__(self, ttl_hours: int = 24, max_entries: int = 10000):
        self._store: Dict[str, CacheEntry] = {}
        self._ttl = ttl_hours * 3600
        self._max = max_entries
        self._total_hits = 0
        self._total_misses = 0

    def get(self, wallet: str) -> Optional[Dict]:
        """Get cached analytics for a wallet. Returns a copy (safe to mutate)."""
        key = self._key(wallet)
        entry = self._store.get(key)

        if entry is None:
            self._total_misses += 1
            return None

        if time.time() > entry.expires_at:
            del self._store[key]
            self._total_misses += 1
            return None

        entry.hits += 1
        self._total_hits += 1
        # Return deep copy to prevent callers from mutating cached data
        import copy
        return copy.deepcopy(entry.data)

    def put(self, wallet: str, analytics: WalletAnalytics):
        """Store analytics in cache."""
        # Evict if full
        if len(self._store) >= self._max:
            self._evict_oldest()

        key = self._key(wallet)
        now = time.time()

        # Convert to serializable dict
        data = self._serialize(analytics)

        self._store[key] = CacheEntry(
            data=data,
            created_at=now,
            expires_at=now + self._ttl,
        )

    def invalidate(self, wallet: str):
        """Remove a wallet from cache."""
        key = self._key(wallet)
        self._store.pop(key, None)

    def clear(self):
        """Clear all cache."""
        self._store.clear()

    def stats(self) -> Dict:
        """Get cache statistics."""
        now = time.time()
        active = sum(1 for e in self._store.values() if now < e.expires_at)
        return {
            "entries": len(self._store),
            "active_entries": active,
            "max_entries": self._max,
            "ttl_hours": self._ttl / 3600,
            "total_hits": self._total_hits,
            "total_misses": self._total_misses,
            "hit_rate": (
                f"{self._total_hits / (self._total_hits + self._total_misses) * 100:.1f}%"
                if (self._total_hits + self._total_misses) > 0 else "0%"
            ),
        }

    def _key(self, wallet: str) -> str:
        return wallet.strip().lower()

    def _evict_oldest(self):
        """Remove oldest entry."""
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
        del self._store[oldest_key]

    def _serialize(self, analytics: WalletAnalytics) -> Dict:
        """Convert WalletAnalytics to JSON-serializable dict."""
        d = {
            "wallet": analytics.wallet,
            "total_trades": analytics.total_trades,
            "total_buys": analytics.total_buys,
            "total_sells": analytics.total_sells,
            "unique_tokens": analytics.unique_tokens,
            "total_realized_pnl_sol": round(analytics.total_realized_pnl_sol, 6),
            "total_sol_spent": round(analytics.total_sol_spent, 6),
            "total_sol_received": round(analytics.total_sol_received, 6),
            "total_fees_sol": round(analytics.total_fees_sol, 6),
            "winning_tokens": analytics.winning_tokens,
            "losing_tokens": analytics.losing_tokens,
            "win_rate": analytics.win_rate,
            "strategy": analytics.strategy,
            "strategy_details": analytics.strategy_details,
            "score": analytics.score,
            "score_breakdown": analytics.score_breakdown,
            "dex_usage": analytics.dex_usage,
            "first_trade_time": analytics.first_trade_time,
            "last_trade_time": analytics.last_trade_time,
            "active_days": analytics.active_days,
            "tokens": [
                {
                    "mint": t.mint,
                    "symbol": t.symbol,
                    "buys": t.buys,
                    "sells": t.sells,
                    "total_bought": round(t.total_bought, 6),
                    "total_sold": round(t.total_sold, 6),
                    "total_sol_spent": round(t.total_sol_spent, 6),
                    "total_sol_received": round(t.total_sol_received, 6),
                    "realized_pnl_sol": round(t.realized_pnl_sol, 6),
                    "avg_buy_price": round(t.avg_buy_price, 12),
                    "avg_sell_price": round(t.avg_sell_price, 12),
                    "hold_time_seconds": t.hold_time_seconds,
                    "is_closed": t.is_closed,
                    "remaining_tokens": round(t.remaining_tokens, 6),
                }
                for t in analytics.tokens
            ],
        }
        return d
