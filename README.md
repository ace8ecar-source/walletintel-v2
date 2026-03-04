# WalletIntel v2

**Free Solana Wallet PnL & Analytics API**

Analyzes any Solana wallet and returns: PnL, Win Rate, Strategy, Score, per-token breakdown.

Built on **free public blockchain data** — zero Helius dependency, zero marginal cost per scan.

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Add your free RPC API keys (Helius, Alchemy, etc.)

# 2. Install & run
pip install -r requirements.txt
python run.py

# 3. Test
curl http://localhost:8000/wallet/YOUR_WALLET_ADDRESS/pnl
```

## Docker

```bash
cp .env.example .env
# edit .env with your keys
docker compose up -d
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Service info |
| `GET /health` | Health check |
| `GET /wallet/{address}/pnl` | Full PnL analysis |
| `GET /wallet/{address}/summary` | Quick summary (no token details) |
| `GET /stats` | Pool & cache stats |

### Parameters

- `max_tx` (int, 10-5000, default 1000): Max transactions to scan
- `force_refresh` (bool, default false): Skip cache

### Response Example

```json
{
  "wallet": "...",
  "total_trades": 142,
  "win_rate": 62.5,
  "total_realized_pnl_sol": 12.45,
  "strategy": "smart_money",
  "score": 74,
  "score_breakdown": {"pnl": 25, "win_rate": 16, "consistency": 14, "volume": 15, "risk": 4},
  "tokens": [...]
}
```

## Architecture

```
Client Request
     ↓
  FastAPI (rate limit, validation)
     ↓
  Cache Check → HIT → Return cached
     ↓ MISS
  RPC Pool (round-robin 5+ free providers)
     ↓
  Transaction Fetcher (signatures → batch getTransaction)
     ↓
  TX Parser (preTokenBalances/postTokenBalances → BUY/SELL)
     ↓
  Analytics Engine (PnL, WR, Strategy, Score)
     ↓
  Cache Store → Return response
```

## Free Tier

- 10 requests/day per IP
- No API key needed
- SOL donations welcome

## License

MIT
