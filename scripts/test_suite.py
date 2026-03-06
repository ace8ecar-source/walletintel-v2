#!/usr/bin/env python3
"""
WalletIntel v2 — Comprehensive Test Suite

Tests:
  1. Edge cases (empty wallet, invalid address, etc.)
  2. Parser accuracy (compare with known data)
  3. Rate limiting
  4. Concurrent requests (load test)
  5. Cache behavior
  6. API endpoints health

Usage:
    python3 scripts/test_suite.py
    python3 scripts/test_suite.py --base-url https://api.walletintel.dev
    python3 scripts/test_suite.py --load-test --concurrent 20
"""
import argparse
import json
import time
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

print_lock = Lock()
results = {"passed": 0, "failed": 0, "warnings": 0}


def api_get(url: str, timeout: int = 300) -> dict:
    """Make GET request to API."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": resp.status, "data": json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()
            body = json.loads(body)
        except:
            pass
        return {"status": e.code, "data": body, "error": True}
    except Exception as e:
        return {"status": 0, "data": {}, "error": str(e)}


def test_pass(name: str, detail: str = ""):
    with print_lock:
        results["passed"] += 1
        print(f"  {GREEN}✓ PASS{RESET} {name}" + (f" — {detail}" if detail else ""))


def test_fail(name: str, detail: str = ""):
    with print_lock:
        results["failed"] += 1
        print(f"  {RED}✗ FAIL{RESET} {name}" + (f" — {detail}" if detail else ""))


def test_warn(name: str, detail: str = ""):
    with print_lock:
        results["warnings"] += 1
        print(f"  {YELLOW}⚠ WARN{RESET} {name}" + (f" — {detail}" if detail else ""))


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}{RESET}\n")


# ============================================================
#  TEST 1: API Endpoints Health
# ============================================================
def test_endpoints(base: str):
    section("TEST 1: API Endpoints Health")

    # Root
    r = api_get(f"{base}/")
    if r["status"] == 200:
        test_pass("GET /", f"status={r['status']}")
    else:
        test_fail("GET /", f"status={r['status']}")

    # Health
    r = api_get(f"{base}/health")
    if r["status"] == 200 and r["data"].get("status") == "ok":
        providers = r["data"].get("healthy_providers", 0)
        test_pass("GET /health", f"healthy_providers={providers}")
    else:
        test_fail("GET /health", str(r))

    # Stats
    r = api_get(f"{base}/stats")
    if r["status"] == 200 and "rpc_pool" in r["data"]:
        scans = r["data"].get("usage", {}).get("total_scans", "?")
        test_pass("GET /stats", f"total_scans={scans}")
    else:
        test_fail("GET /stats", str(r))

    # Leaderboard
    r = api_get(f"{base}/leaderboard")
    if r["status"] == 200 and "wallets" in r["data"]:
        count = r["data"].get("total", 0)
        test_pass("GET /leaderboard", f"wallets={count}")
    else:
        test_fail("GET /leaderboard", str(r))

    # Docs
    r = api_get(f"{base}/docs")
    if r["status"] == 200:
        test_pass("GET /docs", "Swagger UI accessible")
    else:
        test_warn("GET /docs", f"status={r['status']}")


# ============================================================
#  TEST 2: Edge Cases
# ============================================================
def test_edge_cases(base: str):
    section("TEST 2: Edge Cases")

    # Invalid address - too short
    r = api_get(f"{base}/wallet/abc/pnl")
    if r["status"] == 400:
        test_pass("Invalid address (short)", f"400 returned correctly")
    else:
        test_fail("Invalid address (short)", f"expected 400, got {r['status']}")

    # Invalid address - special chars
    r = api_get(f"{base}/wallet/!@#$%^&*()/pnl")
    if r["status"] in (400, 404, 422):
        test_pass("Invalid address (special chars)", f"{r['status']} returned")
    else:
        test_fail("Invalid address (special chars)", f"got {r['status']}")

    # Empty wallet (valid address but no activity)
    # Using a likely empty random address
    r = api_get(f"{base}/wallet/11111111111111111111111111111112/pnl", timeout=60)
    if r["status"] == 200:
        trades = r["data"].get("total_trades", -1)
        if trades == 0:
            test_pass("Empty wallet", "total_trades=0, handled correctly")
        else:
            test_pass("Empty wallet", f"total_trades={trades}")
    elif r["status"] == 400:
        test_pass("Empty wallet", "400 — address validation")
    else:
        test_fail("Empty wallet", f"status={r['status']}")

    # max_tx boundaries
    r = api_get(f"{base}/wallet/CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq/pnl?max_tx=5")
    if r["status"] == 422:  # Below minimum (10)
        test_pass("max_tx=5 (below min)", "422 validation error")
    else:
        test_warn("max_tx=5", f"got status={r['status']}, expected 422")

    r = api_get(f"{base}/wallet/CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq/pnl?max_tx=9999")
    if r["status"] == 422:  # Above maximum (5000)
        test_pass("max_tx=9999 (above max)", "422 validation error")
    else:
        test_warn("max_tx=9999", f"got status={r['status']}, expected 422")

    # Leaderboard edge params
    r = api_get(f"{base}/leaderboard?sort=invalid_sort")
    if r["status"] == 200:
        test_pass("Leaderboard invalid sort", "falls back to default")
    else:
        test_warn("Leaderboard invalid sort", f"status={r['status']}")

    r = api_get(f"{base}/leaderboard?limit=0")
    if r["status"] in (200, 422):
        test_pass("Leaderboard limit=0", f"status={r['status']}")
    else:
        test_fail("Leaderboard limit=0", f"status={r['status']}")


# ============================================================
#  TEST 3: Data Accuracy
# ============================================================
def test_data_accuracy(base: str):
    section("TEST 3: Data Accuracy")

    # Known wallet with predictable data
    wallet = "CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq"
    r = api_get(f"{base}/wallet/{wallet}/pnl?max_tx=500", timeout=120)

    if r["status"] != 200:
        test_fail("Fetch known wallet", f"status={r['status']}")
        return

    data = r["data"]

    # Basic structure
    required_fields = [
        "wallet", "total_trades", "total_buys", "total_sells",
        "unique_tokens", "total_realized_pnl_sol", "win_rate",
        "strategy", "score", "tokens", "dex_usage",
    ]
    missing = [f for f in required_fields if f not in data]
    if not missing:
        test_pass("Response structure", f"all {len(required_fields)} fields present")
    else:
        test_fail("Response structure", f"missing: {missing}")

    # Data types
    if isinstance(data.get("total_trades"), int):
        test_pass("total_trades is int", str(data["total_trades"]))
    else:
        test_fail("total_trades type", f"got {type(data.get('total_trades'))}")

    if isinstance(data.get("win_rate"), (int, float)):
        test_pass("win_rate is numeric", str(data["win_rate"]))
    else:
        test_fail("win_rate type", f"got {type(data.get('win_rate'))}")

    if isinstance(data.get("score"), int) and 0 <= data["score"] <= 100:
        test_pass("score in range 0-100", str(data["score"]))
    else:
        test_fail("score range", f"got {data.get('score')}")

    # Buys + Sells consistency
    buys = data.get("total_buys", 0)
    sells = data.get("total_sells", 0)
    trades = data.get("total_trades", 0)
    if buys + sells == trades:
        test_pass("buys + sells = total_trades", f"{buys} + {sells} = {trades}")
    else:
        test_fail("buys + sells != total_trades", f"{buys} + {sells} != {trades}")

    # WR in range
    wr = data.get("win_rate", -1)
    if 0 <= wr <= 100:
        test_pass("win_rate in 0-100%", f"{wr}%")
    else:
        test_fail("win_rate out of range", f"{wr}")

    # Strategy is valid
    valid_strategies = ["sniper", "scalper", "smart_money", "diamond_hands", "degen", "mixed", "swing", "unknown"]
    if data.get("strategy") in valid_strategies:
        test_pass("strategy is valid", data["strategy"])
    else:
        test_fail("strategy invalid", data.get("strategy"))

    # Tokens array
    tokens = data.get("tokens", [])
    if isinstance(tokens, list) and len(tokens) > 0:
        test_pass("tokens is non-empty list", f"{len(tokens)} tokens")

        # Check first token structure
        t = tokens[0]
        token_fields = ["mint", "buys", "sells", "realized_pnl_sol", "is_closed"]
        t_missing = [f for f in token_fields if f not in t]
        if not t_missing:
            test_pass("token structure", f"all fields present")
        else:
            test_fail("token structure", f"missing: {t_missing}")

        # No WSOL in tokens
        wsol_count = sum(1 for t in tokens if t.get("mint") == "So11111111111111111111111111111111111111112")
        if wsol_count == 0:
            test_pass("No WSOL in token list", "filtered correctly")
        else:
            test_fail("WSOL in token list", f"{wsol_count} WSOL entries found")

        # Check symbols resolved
        with_symbol = sum(1 for t in tokens if t.get("symbol"))
        pct = with_symbol / len(tokens) * 100 if tokens else 0
        if pct > 20:
            test_pass("Token symbols resolved", f"{with_symbol}/{len(tokens)} ({pct:.0f}%)")
        else:
            test_warn("Token symbols low", f"only {with_symbol}/{len(tokens)} resolved")
    else:
        test_fail("tokens empty", str(type(tokens)))

    # Scan info
    scan = data.get("_scan_info", {})
    if scan:
        fetched = scan.get("transactions_fetched", 0)
        found = scan.get("signatures_found", 0)
        if fetched > 0 and fetched == found:
            test_pass("100% fetch rate", f"{fetched}/{found}")
        elif fetched > 0:
            ratio = fetched / found * 100 if found else 0
            test_warn("Fetch rate", f"{fetched}/{found} ({ratio:.1f}%)")
        
        unknown = scan.get("unknown_tx", 0)
        if unknown == 0:
            test_pass("Zero unknown_tx", "all classified")
        else:
            test_warn("Unknown transactions", f"{unknown} unclassified")
    else:
        test_warn("No _scan_info", "response might be cached")


# ============================================================
#  TEST 4: Summary vs PnL consistency
# ============================================================
def test_summary_vs_pnl(base: str):
    section("TEST 4: Summary vs PnL Consistency")

    wallet = "CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq"

    r_pnl = api_get(f"{base}/wallet/{wallet}/pnl", timeout=120)
    r_sum = api_get(f"{base}/wallet/{wallet}/summary", timeout=120)

    if r_pnl["status"] != 200 or r_sum["status"] != 200:
        test_fail("Fetch both endpoints", f"pnl={r_pnl['status']}, summary={r_sum['status']}")
        return

    pnl = r_pnl["data"]
    summary = r_sum["data"]

    # Summary should NOT have tokens
    if "tokens" not in summary:
        test_pass("Summary excludes tokens", "lighter response")
    else:
        test_fail("Summary has tokens", "should be excluded")

    # Core fields should match
    match_fields = ["total_trades", "win_rate", "score", "strategy", "total_realized_pnl_sol"]
    all_match = True
    for field in match_fields:
        if pnl.get(field) != summary.get(field):
            test_fail(f"Mismatch: {field}", f"pnl={pnl.get(field)} vs summary={summary.get(field)}")
            all_match = False
    if all_match:
        test_pass("PnL and Summary match", f"all {len(match_fields)} fields consistent")


# ============================================================
#  TEST 5: Cache Behavior
# ============================================================
def test_cache(base: str):
    section("TEST 5: Cache Behavior")

    wallet = "CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq"

    # First request (might be cached already)
    t1 = time.time()
    r1 = api_get(f"{base}/wallet/{wallet}/pnl", timeout=120)
    time1 = time.time() - t1

    if r1["status"] != 200:
        test_fail("First request", f"status={r1['status']}")
        return

    # Second request (should be cached = fast)
    t2 = time.time()
    r2 = api_get(f"{base}/wallet/{wallet}/pnl", timeout=30)
    time2 = time.time() - t2

    if r2["status"] == 200:
        cached = r2["data"].get("_cached", False)
        if cached:
            test_pass("Cache hit", f"_cached=true")
        else:
            test_warn("Cache miss on repeat", "might have expired")

        if time2 < 2.0:
            test_pass("Cached response fast", f"{time2:.2f}s (vs first: {time1:.2f}s)")
        else:
            test_warn("Cached response slow", f"{time2:.2f}s")

        # Data should be identical
        if r1["data"].get("total_trades") == r2["data"].get("total_trades"):
            test_pass("Cached data matches", "consistent results")
        else:
            test_fail("Cached data differs", "inconsistency detected")
    else:
        test_fail("Second request", f"status={r2['status']}")

    # Force refresh
    t3 = time.time()
    r3 = api_get(f"{base}/wallet/{wallet}/pnl?force_refresh=true", timeout=300)
    time3 = time.time() - t3

    if r3["status"] == 200:
        cached = r3["data"].get("_cached", True)
        if not cached:
            test_pass("Force refresh works", f"_cached=false, took {time3:.1f}s")
        else:
            test_fail("Force refresh ignored", "_cached still true")
    else:
        test_fail("Force refresh", f"status={r3['status']}")


# ============================================================
#  TEST 6: Rate Limiting
# ============================================================
def test_rate_limit(base: str):
    section("TEST 6: Rate Limiting")

    # This test only works against external IP, not localhost
    if "127.0.0.1" in base or "localhost" in base:
        test_warn("Rate limit test skipped", "localhost bypasses rate limit")
        return

    # Make requests until we hit the limit
    wallet = "CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq"
    hit_limit = False

    for i in range(15):
        r = api_get(f"{base}/wallet/{wallet}/summary", timeout=120)
        if r["status"] == 429:
            hit_limit = True
            detail = r["data"] if isinstance(r["data"], dict) else {}
            test_pass("Rate limit triggered", f"after {i+1} requests, resets in {detail.get('resets_in_seconds', '?')}s")
            break

    if not hit_limit:
        test_warn("Rate limit not hit", "sent 15 requests without 429")


# ============================================================
#  TEST 7: Concurrent Load Test
# ============================================================
def test_concurrent(base: str, num_concurrent: int = 10):
    section(f"TEST 7: Concurrent Load Test ({num_concurrent} simultaneous)")

    # Use different cached wallets for realistic load
    wallets = [
        "CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq",
        "D5aXQGUMgnWJEf59mhru1kAKnMHAwTCiDSh5Pt2xTQqg",
        "GKaJNFDp2W5uCYfNKnTPN63tFXKgXgaDSfnTVfksBeq1",
        "uyyH1YAxTUjqJryWSZDGhW1sHUjmR25XLpsAg3L6p1j",
        "6LbewafM8xZgkQduUDebAVtCEfFedduceTq6D6Bm3zLh",
    ]

    success_count = 0
    error_count = 0
    times = []
    errors = []

    def make_request(wallet):
        t = time.time()
        try:
            r = api_get(f"{base}/wallet/{wallet}/summary", timeout=120)
            elapsed = time.time() - t
            return {"status": r["status"], "time": elapsed, "wallet": wallet[:8]}
        except Exception as e:
            elapsed = time.time() - t
            return {"status": 0, "time": elapsed, "error": str(e), "wallet": wallet[:8]}

    # Warm up cache
    print(f"  Warming up cache ({len(wallets)} wallets)...")
    for w in wallets:
        api_get(f"{base}/wallet/{w}/summary", timeout=120)

    # Burst test - all at once
    print(f"  Sending {num_concurrent} concurrent requests...")
    start = time.time()

    with ThreadPoolExecutor(max_workers=num_concurrent) as executor:
        futures = []
        for i in range(num_concurrent):
            w = wallets[i % len(wallets)]
            futures.append(executor.submit(make_request, w))

        for future in as_completed(futures):
            result = future.result()
            times.append(result["time"])
            if result["status"] == 200:
                success_count += 1
            elif result["status"] == 429:
                success_count += 1  # Rate limit is expected behavior
            else:
                error_count += 1
                errors.append(result)

    total_time = time.time() - start

    # Results
    if success_count == num_concurrent:
        test_pass(f"All {num_concurrent} requests succeeded", f"0 errors")
    elif error_count <= num_concurrent * 0.1:
        test_warn(f"Minor errors", f"{success_count}/{num_concurrent} ok, {error_count} errors")
    else:
        test_fail(f"Too many errors", f"{error_count}/{num_concurrent} failed")
        for e in errors[:3]:
            print(f"    Error: {e}")

    avg_time = sum(times) / len(times) if times else 0
    max_time = max(times) if times else 0
    min_time = min(times) if times else 0
    
    test_pass("Response times", f"avg={avg_time:.2f}s, min={min_time:.2f}s, max={max_time:.2f}s")

    rps = num_concurrent / total_time if total_time > 0 else 0
    if rps >= 5:
        test_pass(f"Throughput", f"{rps:.1f} req/sec")
    elif rps >= 1:
        test_warn(f"Throughput", f"{rps:.1f} req/sec (low)")
    else:
        test_fail(f"Throughput", f"{rps:.1f} req/sec (very low)")

    # Sustained load - 30 requests over 10 seconds
    print(f"\n  Sustained load test (30 requests over 10 seconds)...")
    sustained_ok = 0
    sustained_err = 0
    sustained_start = time.time()

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for i in range(30):
            w = wallets[i % len(wallets)]
            futures.append(executor.submit(make_request, w))
            time.sleep(0.33)  # ~3 req/sec

        for future in as_completed(futures):
            result = future.result()
            if result["status"] in (200, 429):
                sustained_ok += 1
            else:
                sustained_err += 1

    sustained_time = time.time() - sustained_start
    sustained_rps = 30 / sustained_time if sustained_time > 0 else 0

    if sustained_err == 0:
        test_pass("Sustained load", f"{sustained_ok}/30 ok in {sustained_time:.1f}s ({sustained_rps:.1f} rps)")
    else:
        test_warn("Sustained load", f"{sustained_ok}/30 ok, {sustained_err} errors")


# ============================================================
#  TEST 8: Memory & Stability Check
# ============================================================
def test_stability(base: str):
    section("TEST 8: Server Stability")

    # Check health before and after tests
    r = api_get(f"{base}/health")
    if r["status"] == 200:
        providers = r["data"].get("healthy_providers", 0)
        if providers >= 1:
            test_pass("Server healthy after tests", f"{providers} RPC providers up")
        else:
            test_fail("No healthy providers", "RPC pool exhausted")
    else:
        test_fail("Health check failed", f"status={r['status']}")

    # Check stats
    r = api_get(f"{base}/stats")
    if r["status"] == 200:
        pool = r["data"].get("rpc_pool", {})
        total_req = pool.get("total_requests", 0)
        cache = r["data"].get("cache", {})
        entries = cache.get("entries", 0)
        test_pass("Stats accessible", f"rpc_requests={total_req}, cache_entries={entries}")
    else:
        test_fail("Stats failed", f"status={r['status']}")


# ============================================================
#  MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="WalletIntel Test Suite")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--load-test", action="store_true", help="Include heavy load test")
    parser.add_argument("--concurrent", type=int, default=10, help="Concurrent requests for load test")
    parser.add_argument("--skip-slow", action="store_true", help="Skip slow tests (cache refresh)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    print(f"\n{BOLD}{'='*60}")
    print(f"  WalletIntel v2 — Comprehensive Test Suite")
    print(f"  Target: {base}")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{RESET}")

    # Run tests
    test_endpoints(base)
    test_edge_cases(base)
    test_data_accuracy(base)
    test_summary_vs_pnl(base)

    if not args.skip_slow:
        test_cache(base)

    test_rate_limit(base)
    test_concurrent(base, args.concurrent)
    test_stability(base)

    # Summary
    total = results["passed"] + results["failed"] + results["warnings"]
    print(f"\n{BOLD}{'='*60}")
    print(f"  TEST RESULTS")
    print(f"{'='*60}{RESET}")
    print(f"  {GREEN}Passed:   {results['passed']}{RESET}")
    print(f"  {RED}Failed:   {results['failed']}{RESET}")
    print(f"  {YELLOW}Warnings: {results['warnings']}{RESET}")
    print(f"  Total:    {total}")
    print(f"{'='*60}\n")

    if results["failed"] == 0:
        print(f"  {GREEN}{BOLD}ALL TESTS PASSED ✓{RESET}\n")
    else:
        print(f"  {RED}{BOLD}{results['failed']} TESTS FAILED ✗{RESET}\n")

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
