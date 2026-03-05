"""
WalletIntel v2 — Transaction Fetcher

Fetches all transactions for a Solana wallet:
1. getSignaturesForAddress → list of tx signatures
2. getTransaction (jsonParsed) → full tx data with pre/postTokenBalances

Uses controlled concurrency with retry to avoid 429s and transaction loss.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.rpc.pool import RPCPool

logger = logging.getLogger(__name__)


@dataclass
class FetchProgress:
    """Track scanning progress for status reporting."""
    wallet: str
    total_signatures: int = 0
    fetched_transactions: int = 0
    parsed_swaps: int = 0
    started_at: float = 0.0
    phase: str = "init"  # init -> signatures -> transactions -> parsing -> done


class TransactionFetcher:
    """
    Fetch transaction history for a Solana wallet using free RPC.

    Flow:
        1. getSignaturesForAddress (paginated, 1000 per request)
        2. Filter: only confirmed, skip failed
        3. getTransaction for each (controlled concurrency + retry)
        4. Return raw transaction data for parser
    """

    def __init__(self, rpc_pool: RPCPool, max_signatures: int = 5000):
        self.rpc = rpc_pool
        self.max_signatures = max_signatures

    async def get_signatures(
        self,
        wallet: str,
        limit: int = 5000,
        before: Optional[str] = None,
    ) -> List[Dict]:
        """
        Fetch transaction signatures for a wallet address.
        Paginates automatically (1000 per request).
        """
        all_sigs = []
        last_sig = before
        per_page = 1000

        while len(all_sigs) < limit:
            params = [
                wallet,
                {
                    "limit": min(per_page, limit - len(all_sigs)),
                    "commitment": "confirmed",
                }
            ]
            if last_sig:
                params[1]["before"] = last_sig

            result = await self.rpc.call("getSignaturesForAddress", params)

            if not result or len(result) == 0:
                break

            valid = [
                sig for sig in result
                if sig.get("err") is None
            ]
            all_sigs.extend(valid)

            last_sig = result[-1]["signature"]

            logger.info(
                f"Fetched {len(all_sigs)} signatures for {wallet[:8]}... "
                f"(page: {len(result)}, valid: {len(valid)})"
            )

            if len(result) < per_page:
                break

        return all_sigs[:limit]

    async def get_transactions(
        self,
        signatures: List[str],
        progress: Optional[FetchProgress] = None,
    ) -> List[Optional[Dict]]:
        """
        Fetch full transaction data with controlled concurrency.

        - Semaphore limits parallel requests (avoids 429 flood)
        - Each request has its own retry with exponential backoff
        - Failed requests get a second pass at the end
        - No transaction is silently dropped
        """
        if not signatures:
            return []

        results = [None] * len(signatures)
        max_concurrent = min(5, len(self.rpc.states) * 2)
        semaphore = asyncio.Semaphore(max_concurrent)
        delay_between = 0.08  # 80ms between launches

        logger.info(
            f"Fetching {len(signatures)} transactions "
            f"(concurrency={max_concurrent})"
        )
        start = time.monotonic()

        async def fetch_one(idx: int, sig: str, max_retry: int = 4):
            """Fetch single transaction with retry and backoff."""
            async with semaphore:
                for attempt in range(max_retry):
                    result = await self.rpc.call(
                        "getTransaction",
                        [
                            sig,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed",
                            }
                        ],
                        retry=1,
                    )

                    if result is not None:
                        results[idx] = result
                        return

                    wait = (2 ** attempt) * 0.5
                    await asyncio.sleep(wait)

        # --- First pass: fetch all with concurrency ---
        tasks = []
        for i, sig in enumerate(signatures):
            tasks.append(fetch_one(i, sig))
            if (i + 1) % max_concurrent == 0:
                await asyncio.sleep(delay_between)

        await asyncio.gather(*tasks)

        first_pass_ok = sum(1 for r in results if r is not None)
        first_pass_failed = sum(1 for r in results if r is None)

        elapsed_1 = time.monotonic() - start
        logger.info(
            f"First pass: {first_pass_ok}/{len(signatures)} ok, "
            f"{first_pass_failed} failed in {elapsed_1:.1f}s"
        )

        # Log progress every pass
        if progress:
            progress.fetched_transactions = first_pass_ok

        # --- Second pass: retry failed ones sequentially ---
        if first_pass_failed > 0:
            logger.info(f"Retrying {first_pass_failed} failed transactions (sequential)...")
            await asyncio.sleep(2)

            recovered = 0
            for i, sig in enumerate(signatures):
                if results[i] is not None:
                    continue

                for attempt in range(3):
                    result = await self.rpc.call(
                        "getTransaction",
                        [
                            sig,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed",
                            }
                        ],
                        retry=2,
                    )

                    if result is not None:
                        results[i] = result
                        recovered += 1
                        break

                    await asyncio.sleep(1.0 + attempt)

                await asyncio.sleep(0.3)

            logger.info(f"Second pass recovered: {recovered}/{first_pass_failed}")

        # Final stats
        elapsed = time.monotonic() - start
        success = sum(1 for r in results if r is not None)
        failed = sum(1 for r in results if r is None)

        logger.info(
            f"Fetched {success}/{len(signatures)} transactions "
            f"in {elapsed:.1f}s ({success / max(elapsed, 0.1):.1f} tx/sec)"
            + (f" -- {failed} permanently failed" if failed else "")
        )

        if progress:
            progress.fetched_transactions = success

        return results

    async def fetch_wallet(
        self,
        wallet: str,
        max_tx: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Complete wallet fetch: signatures -> transactions.
        """
        max_tx = max_tx or self.max_signatures
        start = time.monotonic()

        progress = FetchProgress(wallet=wallet, started_at=start)

        # Step 1: Get signatures
        progress.phase = "signatures"
        sigs = await self.get_signatures(wallet, limit=max_tx)
        progress.total_signatures = len(sigs)

        if not sigs:
            return {
                "wallet": wallet,
                "signatures_found": 0,
                "transactions_fetched": 0,
                "transactions": [],
                "fetch_time_seconds": time.monotonic() - start,
            }

        # Step 2: Fetch full transactions
        progress.phase = "transactions"
        sig_strings = [s["signature"] for s in sigs]
        raw_txs = await self.get_transactions(sig_strings, progress)

        # Step 3: Pair signatures with transaction data
        transactions = []
        for sig_info, raw_tx in zip(sigs, raw_txs):
            if raw_tx is not None:
                transactions.append({
                    "signature": sig_info["signature"],
                    "block_time": raw_tx.get("blockTime") or sig_info.get("blockTime"),
                    "slot": raw_tx.get("slot") or sig_info.get("slot"),
                    "meta": raw_tx.get("meta", {}),
                    "transaction": raw_tx.get("transaction", {}),
                })

        elapsed = time.monotonic() - start
        progress.phase = "done"

        logger.info(
            f"Wallet {wallet[:8]}...: {len(sigs)} sigs -> "
            f"{len(transactions)} tx in {elapsed:.1f}s"
        )

        return {
            "wallet": wallet,
            "signatures_found": len(sigs),
            "transactions_fetched": len(transactions),
            "transactions": transactions,
            "fetch_time_seconds": round(elapsed, 2),
        }
