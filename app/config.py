"""
WalletIntel v2 — Configuration
"""
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class RPCProvider:
    name: str
    url: str
    max_rps: float  # requests per second
    priority: int = 1  # higher = preferred
    is_free: bool = True


# =============================================================
#  FREE RPC PROVIDERS
#  Round-robin across all to stay within limits
#  User adds their own API keys in .env
# =============================================================
DEFAULT_PROVIDERS = [
    RPCProvider(
        name="helius-free",
        url="https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
        max_rps=10,
        priority=3,
    ),
    RPCProvider(
        name="alchemy-free",
        url="https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}",
        max_rps=10,
        priority=2,
    ),
    RPCProvider(
        name="chainstack-free",
        url="{CHAINSTACK_URL}",
        max_rps=10,
        priority=2,
    ),
    RPCProvider(
        name="quicknode-free",
        url="{QUICKNODE_URL}",
        max_rps=10,
        priority=2,
    ),
    RPCProvider(
        name="solana-public",
        url="https://api.mainnet-beta.solana.com",
        max_rps=4,  # conservative — public endpoint
        priority=1,
    ),
]


@dataclass
class Settings:
    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # --- Database ---
    db_url: str = "postgresql+asyncpg://walletintel:walletintel@localhost:5432/walletintel"

    # --- Cache ---
    cache_ttl_hours: int = 24
    cache_max_wallets: int = 10000

    # --- RPC ---
    rpc_batch_size: int = 50  # transactions per batch request
    rpc_max_concurrent: int = 10  # parallel async requests
    rpc_retry_max: int = 3
    rpc_retry_delay: float = 1.0  # seconds

    # --- Scanning ---
    max_signatures: int = 5000  # max tx to scan per wallet
    signatures_per_request: int = 1000  # getSignaturesForAddress limit

    # --- API ---
    free_daily_limit: int = 10
    api_key_header: str = "X-API-Key"

    # --- Known Program IDs ---
    # Used to identify swap transactions
    PROGRAM_IDS: dict = field(default_factory=lambda: {
        # Jupiter v6
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "jupiter",
        "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcPX7a": "jupiter_v4",
        # Raydium
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "raydium_amm",
        "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "raydium_clmm",
        "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "raydium_cpmm",
        # Orca / Whirlpool
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "orca_whirlpool",
        "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP": "orca",
        # pump.fun
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pumpfun",
        # Meteora
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "meteora_dlmm",
        "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "meteora",
        # System
        "11111111111111111111111111111111": "system",
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "token_program",
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "ata_program",
    })

    # SOL mint (wrapped SOL)
    WSOL_MINT: str = "So11111111111111111111111111111111111111112"

    # Stablecoins for USD value reference
    STABLECOINS: dict = field(default_factory=lambda: {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6),
    })


def load_settings() -> Settings:
    """Load settings from environment variables."""
    s = Settings()
    s.db_url = os.getenv("DATABASE_URL", s.db_url)
    s.debug = os.getenv("DEBUG", "false").lower() == "true"
    s.port = int(os.getenv("PORT", s.port))
    s.cache_ttl_hours = int(os.getenv("CACHE_TTL_HOURS", s.cache_ttl_hours))
    return s


def load_rpc_providers() -> List[RPCProvider]:
    """Build provider list from env vars, skip unconfigured ones."""
    providers = []

    helius_key = os.getenv("HELIUS_API_KEY", "")
    if helius_key:
        providers.append(RPCProvider(
            name="helius-free",
            url=f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
            max_rps=10, priority=3,
        ))

    alchemy_key = os.getenv("ALCHEMY_API_KEY", "")
    if alchemy_key:
        providers.append(RPCProvider(
            name="alchemy-free",
            url=f"https://solana-mainnet.g.alchemy.com/v2/{alchemy_key}",
            max_rps=10, priority=2,
        ))

    chainstack_url = os.getenv("CHAINSTACK_URL", "")
    if chainstack_url:
        providers.append(RPCProvider(
            name="chainstack-free",
            url=chainstack_url,
            max_rps=10, priority=2,
        ))

    quicknode_url = os.getenv("QUICKNODE_URL", "")
    if quicknode_url:
        providers.append(RPCProvider(
            name="quicknode-free",
            url=quicknode_url,
            max_rps=10, priority=2,
        ))

    # Public endpoint — always available as fallback
    providers.append(RPCProvider(
        name="solana-public",
        url="https://api.mainnet-beta.solana.com",
        max_rps=4, priority=0,
    ))

    # Sort by priority (highest first)
    providers.sort(key=lambda p: p.priority, reverse=True)
    return providers
