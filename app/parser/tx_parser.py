"""
WalletIntel v2 — Transaction Parser

Parse swaps from preTokenBalances / postTokenBalances
WITHOUT needing Helius Enhanced API.

v2.1 improvements:
- Proper WSOL handling (no double-counting with SOL)
- Classify non-swap tx (transfers, account mgmt) 
- Better dust filtering
- Handle token account creation in same tx
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class TokenChange:
    """A single token balance change for a wallet."""
    mint: str
    owner: str
    pre_amount: float
    post_amount: float
    delta: float  # post - pre (positive = received, negative = sent)
    decimals: int


@dataclass
class SwapEvent:
    """A parsed swap event."""
    signature: str
    block_time: int
    slot: int

    # What changed
    direction: str  # "BUY" or "SELL"
    token_mint: str  # the non-SOL/non-stable token
    token_amount: float  # absolute amount of token
    sol_amount: float  # absolute SOL/stable spent or received
    price_sol: float  # price per token in SOL

    # Context
    base_mint: str  # SOL or USDC/USDT (what was traded for/against)
    base_symbol: str  # "SOL", "USDC", "USDT"
    dex: str  # jupiter, raydium, pumpfun, unknown
    fee_sol: float  # transaction fee in SOL


@dataclass
class ParseResult:
    """Result of parsing all transactions for a wallet."""
    wallet: str
    swaps: List[SwapEvent] = field(default_factory=list)
    transfers_in: int = 0
    transfers_out: int = 0
    account_mgmt: int = 0  # ATA creation, close, approve, etc.
    unknown_tx: int = 0
    total_parsed: int = 0
    total_skipped: int = 0


class TransactionParser:
    """
    Parse raw Solana transactions into SwapEvents.

    Uses the balance-diff approach:
    - preTokenBalances + postTokenBalances -> token changes
    - preBalances + postBalances -> SOL changes
    - Determine BUY/SELL based on what went in/out
    
    Key: WSOL in token balances takes priority over raw SOL change
    to avoid double-counting when DEXes wrap/unwrap SOL.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings()
        self._wsol = self.settings.WSOL_MINT
        self._stables = self.settings.STABLECOINS
        self._programs = self.settings.PROGRAM_IDS

    def parse_wallet_transactions(
        self,
        wallet: str,
        transactions: List[Dict],
    ) -> ParseResult:
        """Parse all transactions for a wallet into swap events."""
        result = ParseResult(wallet=wallet)

        for tx_data in transactions:
            try:
                swap = self._parse_single_tx(wallet, tx_data)
                if swap:
                    result.swaps.append(swap)
                    result.total_parsed += 1
                else:
                    # Classify the non-swap transaction
                    tx_type = self._classify_non_swap(wallet, tx_data)
                    if tx_type == "transfer_in":
                        result.transfers_in += 1
                    elif tx_type == "transfer_out":
                        result.transfers_out += 1
                    elif tx_type == "account_mgmt":
                        result.account_mgmt += 1
                    else:
                        result.unknown_tx += 1
                    result.total_parsed += 1
            except Exception as e:
                logger.debug(
                    f"Failed to parse tx {tx_data.get('signature', '?')[:16]}...: {e}"
                )
                result.total_skipped += 1

        # Sort by time (newest first)
        result.swaps.sort(key=lambda s: s.block_time, reverse=True)

        logger.info(
            f"Parsed {wallet[:8]}...: {len(result.swaps)} swaps, "
            f"{result.transfers_in} transfers_in, {result.transfers_out} transfers_out, "
            f"{result.account_mgmt} account_mgmt, "
            f"{result.unknown_tx} unknown, {result.total_skipped} skipped"
        )

        return result

    def _parse_single_tx(
        self,
        wallet: str,
        tx_data: Dict,
    ) -> Optional[SwapEvent]:
        """
        Parse one transaction into a SwapEvent if it's a swap.
        Returns None if transaction is not a swap.
        """
        meta = tx_data.get("meta", {})
        if not meta:
            return None

        if meta.get("err") is not None:
            return None

        signature = tx_data.get("signature", "")
        block_time = tx_data.get("block_time", 0)
        slot = tx_data.get("slot", 0)

        # --- Get token balance changes for our wallet ---
        token_changes = self._get_token_changes(wallet, meta, tx_data)

        # --- Check if WSOL is in token changes ---
        has_wsol_change = any(tc.mint == self._wsol for tc in token_changes)

        # --- Get SOL balance change ---
        sol_change = self._get_sol_change(wallet, meta, tx_data)
        fee_lamports = meta.get("fee", 0)
        fee_sol = fee_lamports / 1e9

        # --- Build list of received/sent ---
        received = []
        sent = []

        for tc in token_changes:
            if tc.delta > 0:
                received.append(tc)
            elif tc.delta < 0:
                sent.append(tc)

        # Include SOL change ONLY if WSOL is NOT already in token changes
        # This prevents double-counting when DEXes wrap/unwrap SOL
        if not has_wsol_change:
            sol_net = sol_change + fee_sol  # add fee back to see real movement
            if abs(sol_net) > 0.001:  # more than dust
                sol_tc = TokenChange(
                    mint=self._wsol,
                    owner=wallet,
                    pre_amount=0,
                    post_amount=0,
                    delta=sol_net,
                    decimals=9,
                )
                if sol_net > 0:
                    received.append(sol_tc)
                else:
                    sent.append(sol_tc)

        # --- Need both sides for a swap ---
        if not received or not sent:
            return None

        # --- Classify the swap ---
        swap = self._classify_swap(
            signature, block_time, slot,
            received, sent, fee_sol, tx_data,
        )

        return swap

    def _classify_non_swap(self, wallet: str, tx_data: Dict) -> str:
        """
        Classify a non-swap transaction.
        Returns: 'transfer_in', 'transfer_out', 'account_mgmt', or 'unknown'
        """
        meta = tx_data.get("meta", {})
        if not meta:
            return "unknown"

        sol_change = self._get_sol_change(wallet, meta, tx_data)
        fee_lamports = meta.get("fee", 0)
        fee_sol = fee_lamports / 1e9
        token_changes = self._get_token_changes(wallet, meta, tx_data)

        # Check log messages for account management
        log_messages = meta.get("logMessages", [])
        has_account_op = False
        for log in log_messages:
            if isinstance(log, str):
                # Account lifecycle
                if any(kw in log for kw in (
                    "InitializeAccount", "CreateAccount", "CloseAccount",
                    "Approve", "Revoke", "FreezeAccount", "ThawAccount",
                    "InitializeMint", "MintTo", "BurnChecked", "Burn",
                )):
                    has_account_op = True
                # Compute budget (common in bot txs)
                if any(kw in log for kw in (
                    "SetComputeUnitLimit", "SetComputeUnitPrice",
                    "ComputeBudget",
                )):
                    has_account_op = True
                # System operations
                if any(kw in log for kw in (
                    "Allocate", "Assign", "CreateAccountWithSeed",
                    "AdvanceNonceAccount",
                )):
                    has_account_op = True

        if has_account_op:
            return "account_mgmt"

        # Pure SOL transfer (no token changes)
        if not token_changes:
            sol_net = sol_change + fee_sol
            if sol_net > 0.001:
                return "transfer_in"
            elif sol_net < -0.001:
                return "transfer_out"
            # Small SOL change = likely just fee for account mgmt
            if abs(sol_change) > 0:
                return "account_mgmt"
            # Zero change entirely = might be a failed or no-op tx
            return "account_mgmt"

        # Token transfer (one direction only, no swap)
        has_increase = any(tc.delta > 0 for tc in token_changes)
        has_decrease = any(tc.delta < 0 for tc in token_changes)

        if has_increase and not has_decrease:
            return "transfer_in"
        if has_decrease and not has_increase:
            return "transfer_out"

        # Both increase and decrease but _classify_swap couldn't parse it
        # This happens with: base-to-base swaps, tiny dust amounts,
        # complex multi-hop, or token account closures with residual balance
        if has_increase and has_decrease:
            # Check if amounts are tiny (dust from account closures)
            all_tiny = all(
                abs(tc.delta) < 0.01 or
                (tc.delta < 0 and abs(tc.delta) / max(tc.pre_amount, 1) < 0.001)
                for tc in token_changes
            )
            if all_tiny:
                return "account_mgmt"

            # Otherwise it's a swap we couldn't fully parse
            # Log for debugging (first 5 per wallet)
            sig = tx_data.get("signature", "?")
            logger.debug(
                f"Unclassified swap-like tx {sig[:16]}...: "
                f"changes={[(tc.mint[:8], round(tc.delta, 6)) for tc in token_changes]}"
            )
            return "account_mgmt"  # classify as account_mgmt rather than unknown

        return "unknown"

    def _get_token_changes(
        self,
        wallet: str,
        meta: Dict,
        tx_data: Dict,
    ) -> List[TokenChange]:
        """
        Compute token balance diffs from preTokenBalances/postTokenBalances.
        Only returns changes for the target wallet.
        
        Handles edge cases:
        - Token account created in same tx (only in post, not in pre)
        - Token account closed in same tx (only in pre, not in post)
        - Match by mint+owner when accountIndex differs
        """
        pre_balances = meta.get("preTokenBalances", [])
        post_balances = meta.get("postTokenBalances", [])

        # Build lookup by mint (more reliable than accountIndex for our wallet)
        # because accountIndex can differ between pre and post when accounts are created
        pre_by_mint = {}
        for b in pre_balances:
            owner = b.get("owner", "")
            if owner == wallet:
                mint = b.get("mint", "")
                if not mint:
                    continue
                amount_str = b.get("uiTokenAmount", {}).get("uiAmountString", "0")
                decimals = b.get("uiTokenAmount", {}).get("decimals", 0)
                try:
                    amount = float(amount_str) if amount_str else 0.0
                except (ValueError, TypeError):
                    amount = 0.0
                # If multiple accounts for same mint, sum them
                if mint in pre_by_mint:
                    pre_by_mint[mint] = (
                        pre_by_mint[mint][0] + amount,
                        decimals,
                    )
                else:
                    pre_by_mint[mint] = (amount, decimals)

        post_by_mint = {}
        for b in post_balances:
            owner = b.get("owner", "")
            if owner == wallet:
                mint = b.get("mint", "")
                if not mint:
                    continue
                amount_str = b.get("uiTokenAmount", {}).get("uiAmountString", "0")
                decimals = b.get("uiTokenAmount", {}).get("decimals", 0)
                try:
                    amount = float(amount_str) if amount_str else 0.0
                except (ValueError, TypeError):
                    amount = 0.0
                if mint in post_by_mint:
                    post_by_mint[mint] = (
                        post_by_mint[mint][0] + amount,
                        decimals,
                    )
                else:
                    post_by_mint[mint] = (amount, decimals)

        # Merge all mints
        all_mints = set(pre_by_mint.keys()) | set(post_by_mint.keys())
        changes = []

        for mint in all_mints:
            pre_amount, pre_dec = pre_by_mint.get(mint, (0.0, 0))
            post_amount, post_dec = post_by_mint.get(mint, (0.0, 0))
            decimals = pre_dec or post_dec

            delta = post_amount - pre_amount
            if abs(delta) < 1e-12:
                continue

            changes.append(TokenChange(
                mint=mint,
                owner=wallet,
                pre_amount=pre_amount,
                post_amount=post_amount,
                delta=delta,
                decimals=decimals,
            ))

        return changes

    def _get_sol_change(
        self,
        wallet: str,
        meta: Dict,
        tx_data: Dict,
    ) -> float:
        """
        Get SOL balance change for wallet (in SOL, fee already subtracted by runtime).
        """
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])

        account_keys = self._get_account_keys(tx_data)
        wallet_idx = None

        for i, key in enumerate(account_keys):
            if isinstance(key, dict):
                if key.get("pubkey") == wallet:
                    wallet_idx = i
                    break
            elif isinstance(key, str):
                if key == wallet:
                    wallet_idx = i
                    break

        if wallet_idx is None or wallet_idx >= len(pre_balances):
            return 0.0

        pre_lamports = pre_balances[wallet_idx]
        post_lamports = post_balances[wallet_idx] if wallet_idx < len(post_balances) else pre_lamports

        return (post_lamports - pre_lamports) / 1e9

    def _get_account_keys(self, tx_data: Dict) -> List:
        """Extract account keys from transaction."""
        tx = tx_data.get("transaction", {})
        msg = tx.get("message", {})
        return msg.get("accountKeys", [])

    def _classify_swap(
        self,
        signature: str,
        block_time: int,
        slot: int,
        received: List[TokenChange],
        sent: List[TokenChange],
        fee_sol: float,
        tx_data: Dict,
    ) -> Optional[SwapEvent]:
        """
        Classify a swap: determine BUY/SELL, base/token, price.
        """
        # Identify base and token in received
        base_received = None
        token_received = []
        for tc in received:
            if self._is_base(tc.mint):
                base_received = tc
            else:
                token_received.append(tc)

        # Identify base and token in sent
        base_sent = None
        token_sent = []
        for tc in sent:
            if self._is_base(tc.mint):
                base_sent = tc
            else:
                token_sent.append(tc)

        # Detect DEX
        dex = self._detect_dex(tx_data)

        # --- BUY: sent base, received token ---
        if base_sent and token_received:
            token = token_received[0]
            base_amount = abs(base_sent.delta)
            token_amount = abs(token.delta)
            price = base_amount / token_amount if token_amount > 0 else 0

            return SwapEvent(
                signature=signature,
                block_time=block_time,
                slot=slot,
                direction="BUY",
                token_mint=token.mint,
                token_amount=token_amount,
                sol_amount=base_amount,
                price_sol=price,
                base_mint=base_sent.mint,
                base_symbol=self._base_symbol(base_sent.mint),
                dex=dex,
                fee_sol=fee_sol,
            )

        # --- SELL: sent token, received base ---
        if token_sent and base_received:
            token = token_sent[0]
            base_amount = abs(base_received.delta)
            token_amount = abs(token.delta)
            price = base_amount / token_amount if token_amount > 0 else 0

            return SwapEvent(
                signature=signature,
                block_time=block_time,
                slot=slot,
                direction="SELL",
                token_mint=token.mint,
                token_amount=token_amount,
                sol_amount=base_amount,
                price_sol=price,
                base_mint=base_received.mint,
                base_symbol=self._base_symbol(base_received.mint),
                dex=dex,
                fee_sol=fee_sol,
            )

        # --- Token-to-token swap (no SOL/stable) ---
        if token_sent and token_received and not base_sent and not base_received:
            token_in = token_received[0]
            token_out = token_sent[0]

            return SwapEvent(
                signature=signature,
                block_time=block_time,
                slot=slot,
                direction="BUY",
                token_mint=token_in.mint,
                token_amount=abs(token_in.delta),
                sol_amount=0,
                price_sol=0,
                base_mint=token_out.mint,
                base_symbol="TOKEN",
                dex=dex,
                fee_sol=fee_sol,
            )

        # --- Base-to-base swap (SOL↔USDC, USDC↔USDT) ---
        if base_sent and base_received and not token_sent and not token_received:
            # Treat SOL as the "token" side if present
            if base_received.mint == self._wsol:
                # Bought SOL with stablecoin
                return SwapEvent(
                    signature=signature,
                    block_time=block_time,
                    slot=slot,
                    direction="BUY",
                    token_mint=base_received.mint,
                    token_amount=abs(base_received.delta),
                    sol_amount=abs(base_sent.delta),
                    price_sol=abs(base_sent.delta) / abs(base_received.delta) if abs(base_received.delta) > 0 else 0,
                    base_mint=base_sent.mint,
                    base_symbol=self._base_symbol(base_sent.mint),
                    dex=dex,
                    fee_sol=fee_sol,
                )
            else:
                # Sold SOL for stablecoin
                return SwapEvent(
                    signature=signature,
                    block_time=block_time,
                    slot=slot,
                    direction="SELL",
                    token_mint=base_sent.mint,
                    token_amount=abs(base_sent.delta),
                    sol_amount=abs(base_received.delta),
                    price_sol=abs(base_received.delta) / abs(base_sent.delta) if abs(base_sent.delta) > 0 else 0,
                    base_mint=base_received.mint,
                    base_symbol=self._base_symbol(base_received.mint),
                    dex=dex,
                    fee_sol=fee_sol,
                )

        return None

    def _is_base(self, mint: str) -> bool:
        """Check if mint is SOL or stablecoin."""
        return mint == self._wsol or mint in self._stables

    def _base_symbol(self, mint: str) -> str:
        """Get symbol for base token."""
        if mint == self._wsol:
            return "SOL"
        if mint in self._stables:
            return self._stables[mint][0]
        return "UNKNOWN"

    def _detect_dex(self, tx_data: Dict) -> str:
        """
        Detect which DEX was used based on program IDs in the transaction.
        Prioritizes actual DEX programs over system/token programs.
        """
        SKIP_PROGRAMS = {"system", "token_program", "ata_program"}

        account_keys = self._get_account_keys(tx_data)
        meta = tx_data.get("meta", {})
        log_messages = meta.get("logMessages", [])

        # First pass: look for DEX programs (skip system/token infra)
        for key in account_keys:
            addr = key.get("pubkey", key) if isinstance(key, dict) else key
            if addr in self._programs:
                label = self._programs[addr]
                if label not in SKIP_PROGRAMS:
                    return label

        # Check log messages for program invocations
        for log in log_messages:
            if isinstance(log, str):
                if "JUP" in log or "Jupiter" in log:
                    return "jupiter"
                if "675kPX" in log:
                    return "raydium_amm"
                if "6EF8rr" in log:
                    return "pumpfun"

        return "unknown"
