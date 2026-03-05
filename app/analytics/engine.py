"""
WalletIntel v2 — Analytics Engine

Calculates PnL, Win Rate, Strategy, and Wallet Score from SwapEvents.
"""
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.parser.tx_parser import SwapEvent

logger = logging.getLogger(__name__)


@dataclass
class TokenPnL:
    """PnL breakdown for a single token."""
    mint: str
    symbol: str  # populated later from token metadata

    # Trading stats
    buys: int = 0
    sells: int = 0
    total_bought: float = 0.0  # token amount
    total_sold: float = 0.0
    total_sol_spent: float = 0.0  # SOL spent buying
    total_sol_received: float = 0.0  # SOL received selling

    # PnL
    realized_pnl_sol: float = 0.0
    avg_buy_price: float = 0.0
    avg_sell_price: float = 0.0

    # Timing
    first_buy_time: int = 0
    last_sell_time: int = 0
    hold_time_seconds: int = 0

    # Status
    is_closed: bool = False  # all tokens sold
    remaining_tokens: float = 0.0


@dataclass
class WalletAnalytics:
    """Complete analytics for a wallet."""
    wallet: str

    # Summary
    total_trades: int = 0
    total_buys: int = 0
    total_sells: int = 0
    unique_tokens: int = 0

    # PnL
    total_realized_pnl_sol: float = 0.0
    total_sol_spent: float = 0.0
    total_sol_received: float = 0.0
    total_fees_sol: float = 0.0

    # Win Rate
    winning_tokens: int = 0
    losing_tokens: int = 0
    win_rate: float = 0.0  # percentage

    # Strategy
    strategy: str = "unknown"
    strategy_details: Dict = field(default_factory=dict)

    # Scoring
    score: int = 0  # 0-100
    score_breakdown: Dict = field(default_factory=dict)

    # Per-token breakdown
    tokens: List[TokenPnL] = field(default_factory=list)

    # DEX usage
    dex_usage: Dict[str, int] = field(default_factory=dict)

    # Time range
    first_trade_time: int = 0
    last_trade_time: int = 0
    active_days: int = 0


class AnalyticsEngine:
    """Calculate PnL, WR, Strategy, and Score from swap events."""

    # Base tokens to exclude from per-token breakdown
    WSOL = "So11111111111111111111111111111111111111112"
    BASE_MINTS = {
        WSOL,
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    }

    def analyze(self, wallet: str, swaps: List[SwapEvent]) -> WalletAnalytics:
        """Run full analytics pipeline."""
        analytics = WalletAnalytics(wallet=wallet)

        if not swaps:
            return analytics

        # Step 1: Calculate per-token PnL
        token_pnls = self._calculate_token_pnl(swaps)

        # Step 1.5: Remove base tokens (SOL, USDC, USDT) from per-token list
        # These appear from base-to-base swaps and shouldn't be in breakdown
        token_pnls = {
            mint: tp for mint, tp in token_pnls.items()
            if mint not in self.BASE_MINTS
        }

        # Step 2: Aggregate wallet-level stats
        self._aggregate_stats(analytics, swaps, token_pnls)

        # Step 3: Set tokens (needed for score calculation)
        analytics.tokens = sorted(
            token_pnls.values(),
            key=lambda t: t.realized_pnl_sol,
            reverse=True,
        )

        # Step 4: Determine strategy
        analytics.strategy, analytics.strategy_details = self._detect_strategy(
            analytics, swaps, token_pnls
        )

        # Step 5: Calculate score
        analytics.score, analytics.score_breakdown = self._calculate_score(analytics)

        return analytics

    def _calculate_token_pnl(
        self,
        swaps: List[SwapEvent],
    ) -> Dict[str, TokenPnL]:
        """
        Calculate PnL for each token using FIFO cost basis.
        """
        tokens: Dict[str, TokenPnL] = {}

        # Sort by time ascending for FIFO
        sorted_swaps = sorted(swaps, key=lambda s: s.block_time)

        for swap in sorted_swaps:
            mint = swap.token_mint
            if mint not in tokens:
                tokens[mint] = TokenPnL(mint=mint, symbol="")

            tp = tokens[mint]

            if swap.direction == "BUY":
                tp.buys += 1
                tp.total_bought += swap.token_amount
                tp.total_sol_spent += swap.sol_amount

                if tp.first_buy_time == 0:
                    tp.first_buy_time = swap.block_time

                # Update average buy price
                if tp.total_bought > 0:
                    tp.avg_buy_price = tp.total_sol_spent / tp.total_bought

            elif swap.direction == "SELL":
                tp.sells += 1
                tp.total_sold += swap.token_amount
                tp.total_sol_received += swap.sol_amount
                tp.last_sell_time = swap.block_time

                # Update average sell price
                if tp.total_sold > 0:
                    tp.avg_sell_price = tp.total_sol_received / tp.total_sold

        # Calculate realized PnL and status for each token
        for mint, tp in tokens.items():
            # Realized PnL = SOL received from sells - proportional cost of those sells
            if tp.total_sold > 0 and tp.total_bought > 0:
                # Cost basis of sold tokens (FIFO approximation via average)
                cost_of_sold = (tp.total_sold / tp.total_bought) * tp.total_sol_spent
                cost_of_sold = min(cost_of_sold, tp.total_sol_spent)  # can't cost more than spent
                tp.realized_pnl_sol = tp.total_sol_received - cost_of_sold
            else:
                tp.realized_pnl_sol = 0.0

            # Remaining tokens
            tp.remaining_tokens = max(0, tp.total_bought - tp.total_sold)
            tp.is_closed = tp.remaining_tokens < 0.001 * tp.total_bought  # <0.1% remaining

            # Hold time
            if tp.first_buy_time > 0 and tp.last_sell_time > 0:
                tp.hold_time_seconds = tp.last_sell_time - tp.first_buy_time

        return tokens

    def _aggregate_stats(
        self,
        analytics: WalletAnalytics,
        swaps: List[SwapEvent],
        token_pnls: Dict[str, TokenPnL],
    ):
        """Aggregate wallet-level statistics."""
        analytics.total_trades = len(swaps)
        analytics.total_buys = sum(1 for s in swaps if s.direction == "BUY")
        analytics.total_sells = sum(1 for s in swaps if s.direction == "SELL")
        analytics.unique_tokens = len(token_pnls)
        analytics.total_fees_sol = sum(s.fee_sol for s in swaps)

        # PnL totals
        analytics.total_sol_spent = sum(tp.total_sol_spent for tp in token_pnls.values())
        analytics.total_sol_received = sum(tp.total_sol_received for tp in token_pnls.values())
        analytics.total_realized_pnl_sol = sum(tp.realized_pnl_sol for tp in token_pnls.values())

        # Win rate (only count tokens that have been sold)
        closed_tokens = [tp for tp in token_pnls.values() if tp.sells > 0]
        if closed_tokens:
            analytics.winning_tokens = sum(1 for tp in closed_tokens if tp.realized_pnl_sol > 0)
            analytics.losing_tokens = sum(1 for tp in closed_tokens if tp.realized_pnl_sol <= 0)
            analytics.win_rate = round(
                analytics.winning_tokens / len(closed_tokens) * 100, 1
            )

        # DEX usage
        dex_counts = defaultdict(int)
        for s in swaps:
            dex_counts[s.dex] += 1
        analytics.dex_usage = dict(dex_counts)

        # Time range
        times = [s.block_time for s in swaps if s.block_time > 0]
        if times:
            analytics.first_trade_time = min(times)
            analytics.last_trade_time = max(times)
            analytics.active_days = max(
                1,
                (analytics.last_trade_time - analytics.first_trade_time) // 86400
            )

    def _detect_strategy(
        self,
        analytics: WalletAnalytics,
        swaps: List[SwapEvent],
        token_pnls: Dict[str, TokenPnL],
    ) -> tuple:
        """
        Detect trading strategy based on behavior patterns.

        Strategies:
        - sniper: Very fast buy→sell, high volume, pump.fun focus
        - scalper: Quick trades, small profits, high frequency
        - swing: Medium hold times (hours to days)
        - diamond_hands: Long holds (days to weeks)
        - degen: High volume, many tokens, low win rate
        - smart_money: High win rate, calculated entries, good PnL
        """
        details = {}

        if not swaps:
            return "unknown", details

        # Calculate avg hold time for closed positions
        closed = [tp for tp in token_pnls.values() if tp.is_closed and tp.hold_time_seconds > 0]
        avg_hold = (
            sum(tp.hold_time_seconds for tp in closed) / len(closed)
            if closed else 0
        )
        details["avg_hold_seconds"] = round(avg_hold)

        # Trades per day
        tpd = analytics.total_trades / max(1, analytics.active_days)
        details["trades_per_day"] = round(tpd, 1)

        # pump.fun ratio
        pumpfun_trades = analytics.dex_usage.get("pumpfun", 0)
        pumpfun_ratio = pumpfun_trades / max(1, analytics.total_trades)
        details["pumpfun_ratio"] = round(pumpfun_ratio, 2)

        # Classify
        if avg_hold < 300 and tpd > 10 and pumpfun_ratio > 0.3:
            # < 5 min holds, 10+ trades/day, pump.fun
            return "sniper", details

        if avg_hold < 3600 and tpd > 5:
            # < 1 hour holds, 5+ trades/day
            return "scalper", details

        if analytics.win_rate >= 60 and analytics.total_realized_pnl_sol > 0:
            return "smart_money", details

        if avg_hold > 86400:
            # > 1 day holds
            return "diamond_hands", details

        if avg_hold > 3600:
            return "swing", details

        if analytics.unique_tokens > 20 and analytics.win_rate < 40:
            return "degen", details

        return "mixed", details

    def _calculate_score(self, analytics: WalletAnalytics) -> tuple:
        """
        Calculate wallet score (0-100) based on multiple factors.

        Breakdown:
        - pnl_score (0-30): Overall profitability
        - wr_score (0-25): Win rate
        - consistency_score (0-20): Consistent profits across tokens
        - volume_score (0-15): Trading volume / activity
        - risk_score (0-10): Risk management (avg loss size vs avg win)
        """
        breakdown = {}

        # --- PnL Score (0-30) ---
        roi = 0
        if analytics.total_sol_spent > 0:
            roi = analytics.total_realized_pnl_sol / analytics.total_sol_spent
        # Map ROI to score: 100%+ = 30pts, 0% = 15pts, -100% = 0pts
        pnl_score = max(0, min(30, int(15 + roi * 15)))
        breakdown["pnl"] = pnl_score

        # --- Win Rate Score (0-25) ---
        wr_score = max(0, min(25, int(analytics.win_rate * 0.25)))
        breakdown["win_rate"] = wr_score

        # --- Consistency Score (0-20) ---
        if analytics.tokens:
            profitable = sum(1 for t in analytics.tokens if t.realized_pnl_sol > 0)
            consistency = profitable / len(analytics.tokens) if analytics.tokens else 0
            consistency_score = int(consistency * 20)
        else:
            consistency_score = 0
        breakdown["consistency"] = consistency_score

        # --- Volume Score (0-15) ---
        # More trades = more data = more reliable
        if analytics.total_trades >= 50:
            volume_score = 15
        elif analytics.total_trades >= 20:
            volume_score = 10
        elif analytics.total_trades >= 10:
            volume_score = 7
        elif analytics.total_trades >= 5:
            volume_score = 4
        else:
            volume_score = 1
        breakdown["volume"] = volume_score

        # --- Risk Score (0-10) ---
        winning = [t for t in analytics.tokens if t.realized_pnl_sol > 0]
        losing = [t for t in analytics.tokens if t.realized_pnl_sol < 0]
        if winning and losing:
            avg_win = sum(t.realized_pnl_sol for t in winning) / len(winning)
            avg_loss = abs(sum(t.realized_pnl_sol for t in losing) / len(losing))
            risk_reward = avg_win / avg_loss if avg_loss > 0 else 10
            risk_score = max(0, min(10, int(risk_reward * 3)))
        else:
            risk_score = 5
        breakdown["risk"] = risk_score

        total = pnl_score + wr_score + consistency_score + volume_score + risk_score
        return min(100, total), breakdown
