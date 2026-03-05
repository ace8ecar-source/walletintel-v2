"""
WalletIntel v2 — FastAPI Application

Endpoints:
  GET  /                     → Service info
  GET  /health               → Health check
  GET  /wallet/{address}/pnl → Full wallet analytics
  GET  /wallet/{address}/summary → Quick summary only
  GET  /stats                → Pool and cache stats
"""
import logging
import time
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import load_settings, load_rpc_providers
from app.rpc.pool import RPCPool
from app.rpc.fetcher import TransactionFetcher
from app.parser.tx_parser import TransactionParser
from app.analytics.engine import AnalyticsEngine
from app.cache.memory import WalletCache

logger = logging.getLogger(__name__)

# --- Globals (initialized in lifespan) ---
settings = load_settings()
rpc_pool: Optional[RPCPool] = None
fetcher: Optional[TransactionFetcher] = None
parser: Optional[TransactionParser] = None
analytics: Optional[AnalyticsEngine] = None
cache: Optional[WalletCache] = None

# Simple rate limiter: IP → list of timestamps
_rate_limits: dict = defaultdict(list)
FREE_DAILY_LIMIT = 10
RATE_WINDOW = 86400  # 24 hours


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    global rpc_pool, fetcher, parser, analytics, cache

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Initialize components
    providers = load_rpc_providers()
    rpc_pool = RPCPool(providers)
    await rpc_pool.start()

    fetcher = TransactionFetcher(rpc_pool, max_signatures=settings.max_signatures)
    parser = TransactionParser(settings)
    analytics = AnalyticsEngine()
    cache = WalletCache(
        ttl_hours=settings.cache_ttl_hours,
        max_entries=settings.cache_max_wallets,
    )

    logger.info(
        f"WalletIntel v2 started | "
        f"{len(providers)} RPC providers | "
        f"cache TTL {settings.cache_ttl_hours}h"
    )

    yield

    # Shutdown
    if rpc_pool:
        await rpc_pool.stop()
    logger.info("WalletIntel v2 stopped")


app = FastAPI(
    title="WalletIntel v2",
    description="Solana Wallet PnL & Analytics API — Free, powered by public blockchain data",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ===== Helpers =====

SOLANA_ADDRESS_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def validate_wallet(address: str) -> str:
    """Validate Solana wallet address format."""
    address = address.strip()
    if not SOLANA_ADDRESS_RE.match(address):
        raise HTTPException(400, "Invalid Solana wallet address")
    return address


def check_rate_limit(request: Request):
    """Simple IP-based rate limiting for free tier."""
    ip = request.client.host if request.client else "unknown"
    api_key = request.headers.get("X-API-Key", "")

    # API key holders skip rate limit (for future paid tiers)
    if api_key:
        return

    now = time.time()
    cutoff = now - RATE_WINDOW
    _rate_limits[ip] = [t for t in _rate_limits[ip] if t > cutoff]

    if len(_rate_limits[ip]) >= FREE_DAILY_LIMIT:
        raise HTTPException(
            429,
            detail={
                "error": "Daily limit reached",
                "limit": FREE_DAILY_LIMIT,
                "resets_in_seconds": int(RATE_WINDOW - (now - _rate_limits[ip][0])),
                "tip": "Free tier: 10 requests/day. Donate SOL to support the project!",
            }
        )

    _rate_limits[ip].append(now)


# ===== Endpoints =====

@app.get("/")
async def root():
    return {
        "service": "WalletIntel v2",
        "version": "2.0.0",
        "description": "Free Solana Wallet PnL & Analytics API",
        "endpoints": {
            "wallet_pnl": "/wallet/{address}/pnl",
            "wallet_summary": "/wallet/{address}/summary",
            "health": "/health",
            "stats": "/stats",
        },
        "free_tier": f"{FREE_DAILY_LIMIT} requests/day",
        "donate": "Support development: [SOL wallet address]",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    pool_stats = rpc_pool.get_stats() if rpc_pool else {}
    return {
        "status": "ok",
        "healthy_providers": pool_stats.get("healthy_providers", 0),
        "cache": cache.stats() if cache else {},
    }


@app.get("/wallet/{address}/pnl")
async def wallet_pnl(
    address: str,
    request: Request,
    max_tx: int = Query(default=1000, ge=10, le=5000, description="Max transactions to scan"),
    force_refresh: bool = Query(default=False, description="Skip cache"),
):
    """
    Full wallet PnL analysis.

    Returns: PnL per token, win rate, strategy, score, DEX usage.
    First scan may take 15-60 seconds. Cached results return instantly.
    """
    wallet = validate_wallet(address)
    check_rate_limit(request)

    # Check cache
    if not force_refresh:
        cached = cache.get(wallet)
        if cached:
            cached["_cached"] = True
            return cached

    # Full scan
    start = time.time()

    # 1. Fetch transactions
    fetch_result = await fetcher.fetch_wallet(wallet, max_tx=max_tx)

    if fetch_result["transactions_fetched"] == 0:
        return {
            "wallet": wallet,
            "error": None,
            "total_trades": 0,
            "message": "No transactions found or wallet is empty",
            "fetch_time_seconds": fetch_result["fetch_time_seconds"],
        }

    # 2. Parse swaps
    parse_result = parser.parse_wallet_transactions(
        wallet, fetch_result["transactions"]
    )

    # 3. Calculate analytics
    wallet_analytics = analytics.analyze(wallet, parse_result.swaps)

    # 4. Cache result
    cache.put(wallet, wallet_analytics)

    # 5. Build response
    response = cache.get(wallet)  # get serialized version
    response["_cached"] = False
    response["_scan_info"] = {
        "signatures_found": fetch_result["signatures_found"],
        "transactions_fetched": fetch_result["transactions_fetched"],
        "swaps_detected": len(parse_result.swaps),
        "transfers_in": parse_result.transfers_in,
        "transfers_out": parse_result.transfers_out,
        "account_mgmt": parse_result.account_mgmt,
        "unknown_tx": parse_result.unknown_tx,
        "skipped_tx": parse_result.total_skipped,
        "fetch_time": fetch_result["fetch_time_seconds"],
        "total_time": round(time.time() - start, 2),
    }

    return response


@app.get("/wallet/{address}/summary")
async def wallet_summary(
    address: str,
    request: Request,
    max_tx: int = Query(default=500, ge=10, le=5000),
):
    """
    Quick wallet summary — same data, no per-token breakdown.
    Faster for bots that just need WR/PnL/Score.
    """
    full = await wallet_pnl(address, request, max_tx=max_tx)

    # Strip token details for lighter response
    if isinstance(full, dict):
        full.pop("tokens", None)

    return full


@app.get("/stats")
async def stats():
    """Service statistics."""
    return {
        "rpc_pool": rpc_pool.get_stats() if rpc_pool else {},
        "cache": cache.stats() if cache else {},
    }


# ===== Error handlers =====

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail} if isinstance(exc.detail, str) else exc.detail,
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )
