"""
WalletIntel v2 — RPC Connection Pool

Round-robin across free providers with:
- Per-provider rate limiting
- Automatic failover on errors
- Exponential backoff on 429s
- Health tracking
"""
import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from app.config import RPCProvider

logger = logging.getLogger(__name__)


@dataclass
class ProviderState:
    """Runtime state for each RPC provider."""
    provider: RPCProvider
    request_times: List[float] = field(default_factory=list)
    consecutive_errors: int = 0
    is_healthy: bool = True
    cooldown_until: float = 0.0  # timestamp
    total_requests: int = 0
    total_errors: int = 0


class RPCPool:
    """
    Round-robin RPC pool with rate limiting and failover.

    Usage:
        pool = RPCPool(providers)
        await pool.start()
        result = await pool.call("getTransaction", [sig, opts])
        await pool.stop()
    """

    def __init__(self, providers: List[RPCProvider], timeout: float = 30.0):
        self.states: List[ProviderState] = [
            ProviderState(provider=p) for p in providers
        ]
        self.timeout = timeout
        self._current_idx = 0
        self._lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self):
        """Initialize HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
            ),
            http2=True,
        )
        logger.info(
            f"RPC Pool started with {len(self.states)} providers: "
            f"{[s.provider.name for s in self.states]}"
        )

    async def stop(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _next_available(self) -> Optional[ProviderState]:
        """Get next healthy provider respecting rate limits."""
        now = time.monotonic()
        n = len(self.states)

        for _ in range(n):
            state = self.states[self._current_idx]
            self._current_idx = (self._current_idx + 1) % n

            # Skip unhealthy or in cooldown
            if not state.is_healthy:
                if now < state.cooldown_until:
                    continue
                # Try to recover
                state.is_healthy = True
                state.consecutive_errors = 0
                logger.info(f"RPC {state.provider.name}: recovered from cooldown")

            # Check rate limit
            window = 1.0  # 1 second window
            cutoff = now - window
            state.request_times = [t for t in state.request_times if t > cutoff]

            if len(state.request_times) < state.provider.max_rps:
                return state

        return None

    async def _wait_for_slot(self) -> ProviderState:
        """Wait until a provider has capacity."""
        for attempt in range(100):  # max ~10 seconds
            state = self._next_available()
            if state:
                return state
            await asyncio.sleep(0.1)

        # Emergency: force the provider with most priority
        logger.warning("All providers at capacity, forcing request")
        return self.states[0]

    def _mark_success(self, state: ProviderState):
        state.consecutive_errors = 0
        state.total_requests += 1

    def _mark_error(self, state: ProviderState, is_rate_limit: bool = False):
        state.consecutive_errors += 1
        state.total_errors += 1
        state.total_requests += 1

        if is_rate_limit:
            # Back off this provider
            cooldown = min(2 ** state.consecutive_errors, 60)
            state.cooldown_until = time.monotonic() + cooldown
            state.is_healthy = False
            logger.warning(
                f"RPC {state.provider.name}: rate limited, "
                f"cooldown {cooldown}s"
            )
        elif state.consecutive_errors >= 5:
            state.cooldown_until = time.monotonic() + 30
            state.is_healthy = False
            logger.error(
                f"RPC {state.provider.name}: too many errors, "
                f"cooldown 30s"
            )

    async def call(
        self,
        method: str,
        params: Any = None,
        retry: int = 3,
    ) -> Optional[Dict]:
        """
        Make a single JSON-RPC call with automatic failover.

        Returns the 'result' field or None on complete failure.
        """
        if not self._client:
            raise RuntimeError("RPCPool not started. Call await pool.start()")

        last_error = None

        for attempt in range(retry):
            state = await self._wait_for_slot()
            state.request_times.append(time.monotonic())

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params or [],
            }

            try:
                resp = await self._client.post(
                    state.provider.url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                if resp.status_code == 429:
                    self._mark_error(state, is_rate_limit=True)
                    last_error = "rate_limited"
                    continue

                if resp.status_code == 403:
                    self._mark_error(state, is_rate_limit=True)
                    last_error = "forbidden"
                    continue

                if resp.status_code != 200:
                    self._mark_error(state)
                    last_error = f"http_{resp.status_code}"
                    continue

                data = resp.json()

                if "error" in data:
                    error_code = data["error"].get("code", 0)
                    error_msg = data["error"].get("message", "")

                    # Server overloaded or resource issue
                    if error_code in (-32005, -32016):
                        self._mark_error(state, is_rate_limit=True)
                        last_error = error_msg
                        continue

                    # Real error — don't retry
                    logger.debug(
                        f"RPC error from {state.provider.name}: "
                        f"{error_code} {error_msg}"
                    )
                    self._mark_success(state)
                    return None

                self._mark_success(state)
                return data.get("result")

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                self._mark_error(state)
                last_error = str(e)
                logger.warning(
                    f"RPC {state.provider.name}: {type(e).__name__}: {e}"
                )
                continue

            except Exception as e:
                self._mark_error(state)
                last_error = str(e)
                logger.error(
                    f"RPC {state.provider.name}: unexpected error: {e}"
                )
                continue

        logger.error(f"RPC call {method} failed after {retry} retries: {last_error}")
        return None

    async def call_batch(
        self,
        calls: List[Dict],
        batch_size: int = 50,
    ) -> List[Optional[Dict]]:
        """
        Batch JSON-RPC calls. Splits into chunks and runs in parallel.

        calls: List of {"method": str, "params": list}
        Returns: List of results in same order
        """
        if not calls:
            return []

        results = [None] * len(calls)

        # Split into batches
        batches = []
        for i in range(0, len(calls), batch_size):
            chunk = calls[i:i + batch_size]
            batches.append((i, chunk))

        # Process batches with concurrency limit
        sem = asyncio.Semaphore(self._get_max_concurrent())

        async def process_batch(start_idx: int, batch: List[Dict]):
            async with sem:
                state = await self._wait_for_slot()
                state.request_times.append(time.monotonic())

                payload = [
                    {
                        "jsonrpc": "2.0",
                        "id": start_idx + j,
                        "method": c["method"],
                        "params": c["params"],
                    }
                    for j, c in enumerate(batch)
                ]

                try:
                    resp = await self._client.post(
                        state.provider.url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )

                    if resp.status_code == 429:
                        self._mark_error(state, is_rate_limit=True)
                        # Fallback: send individually
                        for j, c in enumerate(batch):
                            r = await self.call(c["method"], c["params"])
                            results[start_idx + j] = r
                        return

                    if resp.status_code != 200:
                        self._mark_error(state)
                        return

                    data = resp.json()
                    self._mark_success(state)

                    # Batch response might be unordered
                    if isinstance(data, list):
                        for item in data:
                            idx = item.get("id", 0)
                            if isinstance(idx, int) and 0 <= idx < len(results):
                                results[idx] = item.get("result")
                    else:
                        # Some providers don't support batch
                        # Fallback to individual calls
                        for j, c in enumerate(batch):
                            r = await self.call(c["method"], c["params"])
                            results[start_idx + j] = r

                except Exception as e:
                    self._mark_error(state)
                    logger.warning(f"Batch failed on {state.provider.name}: {e}")
                    # Fallback to individual
                    for j, c in enumerate(batch):
                        r = await self.call(c["method"], c["params"])
                        results[start_idx + j] = r

        tasks = [process_batch(si, b) for si, b in batches]
        await asyncio.gather(*tasks)
        return results

    def _get_max_concurrent(self) -> int:
        """Calculate safe concurrency based on available providers."""
        total_rps = sum(
            s.provider.max_rps
            for s in self.states
            if s.is_healthy
        )
        return max(2, int(total_rps * 0.8))

    def get_stats(self) -> Dict:
        """Get pool statistics."""
        return {
            "providers": [
                {
                    "name": s.provider.name,
                    "healthy": s.is_healthy,
                    "total_requests": s.total_requests,
                    "total_errors": s.total_errors,
                    "consecutive_errors": s.consecutive_errors,
                    "error_rate": (
                        f"{s.total_errors / s.total_requests * 100:.1f}%"
                        if s.total_requests > 0 else "0%"
                    ),
                }
                for s in self.states
            ],
            "total_requests": sum(s.total_requests for s in self.states),
            "healthy_providers": sum(1 for s in self.states if s.is_healthy),
        }
