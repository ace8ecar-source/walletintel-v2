"""
WalletIntel v2 — Test Suite

Tests parser + analytics with realistic mock Solana transaction data.
"""
import json
import sys
sys.path.insert(0, '.')

from app.parser.tx_parser import TransactionParser
from app.analytics.engine import AnalyticsEngine
from app.cache.memory import WalletCache
from app.config import Settings

WALLET = "TestWa11etXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
WSOL = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PUMPTOKEN = "PuMpF1111111111111111111111111111111111111"


def make_tx(sig, block_time, pre_sol, post_sol, fee,
            pre_tokens=None, post_tokens=None,
            account_keys=None, programs=None):
    """Build a realistic mock Solana transaction."""
    pre_tokens = pre_tokens or []
    post_tokens = post_tokens or []
    programs = programs or []

    keys = [{"pubkey": WALLET}, {"pubkey": "11111111111111111111111111111111"}]
    for prog in programs:
        keys.append({"pubkey": prog})
    if account_keys:
        keys = account_keys

    return {
        "signature": sig,
        "block_time": block_time,
        "slot": block_time * 2,
        "meta": {
            "err": None,
            "fee": fee,
            "preBalances": [int(pre_sol * 1e9), 0],
            "postBalances": [int(post_sol * 1e9), 0],
            "preTokenBalances": pre_tokens,
            "postTokenBalances": post_tokens,
            "logMessages": [],
        },
        "transaction": {
            "message": {
                "accountKeys": keys,
            }
        }
    }


def token_balance(idx, mint, owner, amount, decimals=9):
    """Build a token balance entry."""
    return {
        "accountIndex": idx,
        "mint": mint,
        "owner": owner,
        "uiTokenAmount": {
            "uiAmountString": str(amount),
            "uiAmount": amount,
            "decimals": decimals,
        }
    }


def test_buy_sol_for_token():
    """Test: Wallet spends SOL, receives BONK = BUY."""
    print("\n=== Test: BUY (SOL → BONK) ===")
    parser = TransactionParser()

    tx = make_tx(
        sig="buy_sig_001",
        block_time=1700000000,
        pre_sol=10.0, post_sol=8.495,  # spent ~1.5 SOL + 0.005 fee
        fee=5000000,  # 0.005 SOL
        pre_tokens=[
            token_balance(1, BONK, WALLET, 0, decimals=5),
        ],
        post_tokens=[
            token_balance(1, BONK, WALLET, 50000, decimals=5),
        ],
        programs=["JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"],
    )

    result = parser.parse_wallet_transactions(WALLET, [tx])
    assert len(result.swaps) == 1, f"Expected 1 swap, got {len(result.swaps)}"

    swap = result.swaps[0]
    assert swap.direction == "BUY", f"Expected BUY, got {swap.direction}"
    assert swap.token_mint == BONK, f"Expected BONK mint, got {swap.token_mint}"
    assert swap.token_amount == 50000, f"Expected 50000 BONK, got {swap.token_amount}"
    assert swap.dex == "jupiter", f"Expected jupiter DEX, got {swap.dex}"

    print(f"  ✓ Direction: {swap.direction}")
    print(f"  ✓ Token: {swap.token_mint[:8]}... amount={swap.token_amount}")
    print(f"  ✓ SOL spent: {swap.sol_amount:.4f}")
    print(f"  ✓ Price: {swap.price_sol:.10f} SOL/token")
    print(f"  ✓ DEX: {swap.dex}")
    print("  PASSED")


def test_sell_token_for_sol():
    """Test: Wallet sends BONK, receives SOL = SELL."""
    print("\n=== Test: SELL (BONK → SOL) ===")
    parser = TransactionParser()

    tx = make_tx(
        sig="sell_sig_001",
        block_time=1700001000,
        pre_sol=8.0, post_sol=10.495,  # received ~2.5 SOL (minus 0.005 fee)
        fee=5000000,
        pre_tokens=[
            token_balance(1, BONK, WALLET, 50000, decimals=5),
        ],
        post_tokens=[
            token_balance(1, BONK, WALLET, 0, decimals=5),
        ],
        programs=["675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"],
    )

    result = parser.parse_wallet_transactions(WALLET, [tx])
    assert len(result.swaps) == 1

    swap = result.swaps[0]
    assert swap.direction == "SELL"
    assert swap.token_mint == BONK
    assert swap.token_amount == 50000

    print(f"  ✓ Direction: {swap.direction}")
    print(f"  ✓ Token amount sold: {swap.token_amount}")
    print(f"  ✓ SOL received: {swap.sol_amount:.4f}")
    print(f"  ✓ DEX: {swap.dex}")
    print("  PASSED")


def test_full_analytics_pipeline():
    """Test: Multiple trades → PnL, WR, Strategy, Score."""
    print("\n=== Test: Full Analytics Pipeline ===")
    parser = TransactionParser()
    engine = AnalyticsEngine()

    transactions = []
    base_time = 1700000000

    # --- Trade 1: BUY 100k BONK for 1 SOL ---
    transactions.append(make_tx(
        sig="trade_001", block_time=base_time,
        pre_sol=10.0, post_sol=8.995, fee=5000000,
        pre_tokens=[token_balance(1, BONK, WALLET, 0, 5)],
        post_tokens=[token_balance(1, BONK, WALLET, 100000, 5)],
        programs=["JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"],
    ))

    # --- Trade 2: SELL 100k BONK for 2 SOL (profit!) ---
    transactions.append(make_tx(
        sig="trade_002", block_time=base_time + 3600,
        pre_sol=8.995, post_sol=10.99, fee=5000000,
        pre_tokens=[token_balance(1, BONK, WALLET, 100000, 5)],
        post_tokens=[token_balance(1, BONK, WALLET, 0, 5)],
        programs=["JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"],
    ))

    # --- Trade 3: BUY PUMP token for 0.5 SOL ---
    transactions.append(make_tx(
        sig="trade_003", block_time=base_time + 7200,
        pre_sol=10.99, post_sol=10.485, fee=5000000,
        pre_tokens=[token_balance(1, PUMPTOKEN, WALLET, 0, 6)],
        post_tokens=[token_balance(1, PUMPTOKEN, WALLET, 1000000, 6)],
        programs=["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"],
    ))

    # --- Trade 4: SELL PUMP token for 0.2 SOL (loss) ---
    transactions.append(make_tx(
        sig="trade_004", block_time=base_time + 7500,
        pre_sol=10.485, post_sol=10.68, fee=5000000,
        pre_tokens=[token_balance(1, PUMPTOKEN, WALLET, 1000000, 6)],
        post_tokens=[token_balance(1, PUMPTOKEN, WALLET, 0, 6)],
        programs=["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"],
    ))

    # Parse
    parse_result = parser.parse_wallet_transactions(WALLET, transactions)
    print(f"  Parsed: {len(parse_result.swaps)} swaps")

    # Analyze
    analytics = engine.analyze(WALLET, parse_result.swaps)

    print(f"  ✓ Total trades: {analytics.total_trades}")
    print(f"  ✓ Buys: {analytics.total_buys}, Sells: {analytics.total_sells}")
    print(f"  ✓ Unique tokens: {analytics.unique_tokens}")
    print(f"  ✓ Win rate: {analytics.win_rate}%")
    print(f"  ✓ Realized PnL: {analytics.total_realized_pnl_sol:.4f} SOL")
    print(f"  ✓ Strategy: {analytics.strategy}")
    print(f"  ✓ Score: {analytics.score}/100")
    print(f"  ✓ Score breakdown: {analytics.score_breakdown}")
    print(f"  ✓ DEX usage: {analytics.dex_usage}")

    # Validate
    assert analytics.total_trades == 4, f"Expected 4 trades, got {analytics.total_trades}"
    assert analytics.unique_tokens == 2, f"Expected 2 tokens, got {analytics.unique_tokens}"
    assert analytics.winning_tokens == 1, f"Expected 1 winning token"
    assert analytics.losing_tokens == 1, f"Expected 1 losing token"
    assert analytics.win_rate == 50.0, f"Expected 50% WR, got {analytics.win_rate}%"
    assert analytics.total_realized_pnl_sol > 0, "Expected positive overall PnL"
    assert 0 <= analytics.score <= 100, f"Score out of range: {analytics.score}"

    # Check per-token breakdown
    bonk_pnl = next(t for t in analytics.tokens if t.mint == BONK)
    pump_pnl = next(t for t in analytics.tokens if t.mint == PUMPTOKEN)

    print(f"\n  Token breakdown:")
    print(f"    BONK: PnL={bonk_pnl.realized_pnl_sol:.4f} SOL, closed={bonk_pnl.is_closed}")
    print(f"    PUMP: PnL={pump_pnl.realized_pnl_sol:.4f} SOL, closed={pump_pnl.is_closed}")

    assert bonk_pnl.realized_pnl_sol > 0, "BONK should be profitable"
    assert pump_pnl.realized_pnl_sol < 0, "PUMP should be a loss"
    assert bonk_pnl.is_closed, "BONK position should be closed"
    assert pump_pnl.is_closed, "PUMP position should be closed"

    print("\n  PASSED")


def test_cache():
    """Test cache store and retrieve."""
    print("\n=== Test: Cache ===")
    engine = AnalyticsEngine()
    cache = WalletCache(ttl_hours=1)

    # Create dummy analytics
    from app.parser.tx_parser import SwapEvent
    swaps = [SwapEvent(
        signature="test", block_time=1700000000, slot=1,
        direction="BUY", token_mint=BONK, token_amount=100,
        sol_amount=1.0, price_sol=0.01, base_mint=WSOL,
        base_symbol="SOL", dex="jupiter", fee_sol=0.005,
    )]
    analytics = engine.analyze(WALLET, swaps)

    # Store
    cache.put(WALLET, analytics)

    # Retrieve
    cached = cache.get(WALLET)
    assert cached is not None, "Cache miss after put"
    assert cached["wallet"] == WALLET
    assert cached["total_trades"] == 1

    # Stats
    stats = cache.stats()
    assert stats["total_hits"] == 1
    print(f"  ✓ Cache hit rate: {stats['hit_rate']}")
    print(f"  ✓ Entries: {stats['entries']}")

    # Miss
    miss = cache.get("NonExistentWallet1111111111111111111111111")
    assert miss is None, "Expected cache miss"

    print("  PASSED")


def test_usdc_swap():
    """Test: Swap using USDC as base instead of SOL."""
    print("\n=== Test: USDC-based swap ===")
    parser = TransactionParser()

    tx = make_tx(
        sig="usdc_swap_001", block_time=1700010000,
        pre_sol=5.0, post_sol=4.995, fee=5000000,
        pre_tokens=[
            token_balance(1, USDC, WALLET, 100.0, 6),
            token_balance(2, BONK, WALLET, 0, 5),
        ],
        post_tokens=[
            token_balance(1, USDC, WALLET, 50.0, 6),
            token_balance(2, BONK, WALLET, 500000, 5),
        ],
    )

    result = parser.parse_wallet_transactions(WALLET, [tx])
    assert len(result.swaps) == 1

    swap = result.swaps[0]
    assert swap.direction == "BUY"
    assert swap.base_symbol == "USDC"
    assert swap.sol_amount == 50.0  # 50 USDC spent

    print(f"  ✓ Direction: {swap.direction}")
    print(f"  ✓ Base: {swap.base_symbol} (spent {swap.sol_amount})")
    print(f"  ✓ Got {swap.token_amount} tokens")
    print("  PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("  WalletIntel v2 — Test Suite")
    print("=" * 60)

    tests = [
        test_buy_sol_for_token,
        test_sell_token_for_sol,
        test_full_analytics_pipeline,
        test_cache,
        test_usdc_swap,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"\n  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")

    sys.exit(0 if failed == 0 else 1)
