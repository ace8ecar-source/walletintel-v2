"""
WalletIntel v2 — Transaction Fetcher

Fetches all transactions for a Solana wallet:
1. getSignaturesForAddress → list of tx signatures
2. getTransaction (jsonParsed) → full tx data with pre/postTokenBalances
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
    phase: str = "init"  # init → signatures → transactions → parsing → done


class TransactionFetcher:
    """
    Fetch transaction history for a Solana wallet using free RPC.

    Flow:
        1. getSignaturesForAddress (paginated, 1000 per request)
        2. Filter: only confirmed, skip failed
        3. getTransaction for each (batch + parallel)
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

        Returns: List of {signature, slot, blockTime, err, memo}
        """
        all_sigs = []
        last_sig = before
        per_page = 1000  # Solana max

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

            # Filter out failed transactions
            valid = [
                sig for sig in result
                if sig.get("err") is None
            ]
            all_sigs.extend(valid)

            # Set pagination cursor
            last_sig = result[-1]["signature"]

            logger.info(
                f"Fetched {len(all_sigs)} signatures for {wallet[:8]}... "
                f"(page: {len(result)}, valid: {len(valid)})"
            )

            # If we got less than requested, no more data
            if len(result) < per_page:
                break

        return all_sigs[:limit]

    async def get_transactions(
        self,
        signatures: List[str],
        progress: Optional[FetchProgress] = None,
    ) -> List[Optional[Dict]]:
        """
        Fetch full transaction data for a list of signatures.
        Uses batch requests + parallel execution for speed.

        Returns transactions with jsonParsed encoding including
        preTokenBalances and postTokenBalances.
        """
        if not signatures:
            return []

        calls = [
            {
                "method": "getTransaction",
                "params": [
                    sig,
                    {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0,
                        "commitment": "confirmed",
                    }
                ],
            }
            for sig in signatures
        ]

        logger.info(
            f"Fetching {len(calls)} transactions "
            f"(batch size: {self.rpc._get_max_concurrent()})"
        )

        start = time.monotonic()
        results = await self.rpc.call_batch(calls, batch_size=50)
        elapsed = time.monotonic() - start

        success = sum(1 for r in results if r is not None)
        logger.info(
            f"Fetched {success}/{len(calls)} transactions in {elapsed:.1f}s "
            f"({success / elapsed:.0f} tx/sec)"
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
        Complete wallet fetch: signatures → transactions.

        Returns:
            {
                "wallet": str,
                "signatures_found": int,
                "transactions_fetched": int,
                "transactions": List[Dict],  # raw tx data
                "fetch_time_seconds": float,
            }
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
            f"Wallet {wallet[:8]}...: {len(sigs)} sigs → "
            f"{len(transactions)} tx in {elapsed:.1f}s"
        )

        return {
            "wallet": wallet,
            "signatures_found": len(sigs),
            "transactions_fetched": len(transactions),
            "transactions": transactions,
            "fetch_time_seconds": round(elapsed, 2),
        }
