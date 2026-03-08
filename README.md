# WalletIntel v2 — Free Solana Wallet PnL API

[![Live API](https://img.shields.io/badge/API-Live-brightgreen)](https://api.walletintel.dev/docs)
[![Website](https://img.shields.io/badge/Website-walletintel.dev-blue)](https://walletintel.dev)
[![Telegram](https://img.shields.io/badge/Telegram-@walletintel__daily-blue)](https://t.me/walletintel_daily)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

**Analyze any Solana wallet in seconds — PnL, Win Rate, Strategy Detection, Score.** Free, open-source, no API key required.

🔗 **Live:** [walletintel.dev](https://walletintel.dev) | **API Docs:** [walletintel.dev/docs](https://walletintel.dev/docs) | **Telegram:** [@walletintel_daily](https://t.me/walletintel_daily)

---

## What is WalletIntel?

WalletIntel is a **free Solana wallet analyzer API** that provides:

- **PnL per token** — Realized profit/loss for every token traded (FIFO cost basis)
- **Win Rate** — Percentage of profitable trades (closed positions only)
- **Strategy Detection** — Sniper, Scalper, Smart Money, Diamond Hands, Degen
- **Wallet Score** — 0-100 rating based on profitability, consistency, and volume
- **Token Resolution** — Auto-resolved symbols via DexScreener
- **DEX Detection** — Jupiter, Raydium, pump.fun, Orca, Meteora

Built for developers building **Solana trading bots**, **copy-trade systems**, **wallet trackers**, and **DeFi analytics dashboards**.

## Quick Start

**No signup. No API key. One HTTP request.**

### cURL

```bash
# Full PnL analysis
curl https://api.walletintel.dev/wallet/YOUR_WALLET_ADDRESS/pnl

# Quick summary (no per-token breakdown)
curl https://api.walletintel.dev/wallet/YOUR_WALLET_ADDRESS/summary

# Top wallets leaderboard
curl https://api.walletintel.dev/leaderboard?sort=score
```

### Python

```python
import requests

wallet = "CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq"
resp = requests.get(f"https://api.walletintel.dev/wallet/{wallet}/pnl")
data = resp.json()

print(f"PnL: {data['total_realized_pnl_sol']} SOL")
print(f"Win Rate: {data['win_rate']}%")
print(f"Strategy: {data['strategy']}")
print(f"Score: {data['score']}/100")
print(f"Tokens traded: {data['unique_tokens']}")

# Per-token breakdown
for token in data['tokens'][:5]:
    symbol = token['symbol'] or token['mint'][:12]
    print(f"  {symbol}: {token['realized_pnl_sol']:+.4f} SOL")
```

### JavaScript

```javascript
const wallet = "CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq";
const resp = await fetch(
  `https://api.walletintel.dev/wallet/${wallet}/pnl`
);
const data = await resp.json();

console.log(`Score: ${data.score}/100`);
console.log(`PnL: ${data.total_realized_pnl_sol} SOL`);
console.log(`Strategy: ${data.strategy}`);
```

### Telegram Bot Example

```python
import requests
import telebot

bot = telebot.TeleBot("YOUR_BOT_TOKEN")

@bot.message_handler(commands=['scan'])
def scan_wallet(message):
    wallet = message.text.split(' ')[1]
    resp = requests.get(
        f"https://api.walletintel.dev/wallet/{wallet}/summary"
    )
    data = resp.json()

    bot.reply_to(message,
        f"🔍 Wallet: {wallet[:8]}...{wallet[-4:]}\n"
        f"📊 Score: {data['score']}/100\n"
        f"🎯 Win Rate: {data['win_rate']}%\n"
        f"💰 PnL: {data['total_realized_pnl_sol']:+.2f} SOL\n"
        f"🧠 Strategy: {data['strategy']}"
    )
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /wallet/{address}/pnl` | Full PnL analysis with per-token breakdown |
| `GET /wallet/{address}/summary` | Quick summary without token details |
| `GET /leaderboard` | Top wallets by score, PnL, win rate |
| `GET /health` | Service health check |
| `GET /stats` | Usage statistics |
| `GET /docs` | Interactive Swagger documentation |

### Parameters

| Parameter | Default | Range | Description |
|---|---|---|---|
| `max_tx` | 1000 | 10-5000 | Max transactions to scan |
| `force_refresh` | false | - | Skip cache, rescan wallet |

### Example Response

```json
{
  "wallet": "CshCAkxi4JZktyHr8Co9DwfJjrt2mFGxewMSYHQtrJMq",
  "total_trades": 60,
  "total_buys": 30,
  "total_sells": 30,
  "unique_tokens": 31,
  "total_realized_pnl_sol": 8.637,
  "win_rate": 43.3,
  "strategy": "mixed",
  "score": 64,
  "dex_usage": {
    "pumpfun": 59,
    "unknown": 1
  },
  "active_days": 39,
  "tokens": [
    {
      "mint": "cXcoMLKQReV9osApm5KU6GLHie8tGAz1Gy9MpBapump",
      "symbol": "ZOOFIGHTER",
      "buys": 1,
      "sells": 1,
      "total_sol_spent": 0.833,
      "total_sol_received": 2.283,
      "realized_pnl_sol": 1.45,
      "is_closed": true,
      "hold_time_seconds": 49
    }
  ]
}
```

## Use Cases

### 🤖 Copy-Trade Bot
Scan wallets from the leaderboard, filter by score > 80 and win rate > 70%, then monitor their new trades.

### 🔔 Smart Money Alerts
Periodically rescan top wallets — when PnL changes, a whale made a new trade. Send Telegram/Discord alerts.

### 📊 Analytics Dashboard
Embed wallet intelligence into your own DeFi dashboard. Show users their PnL, strategy, and score.

### 🎯 Wallet Filtering
Before following a wallet, check if it's actually profitable. Filter out bots, scammers, and random wallets.

## Architecture

```
Client → nginx (HTTPS) → FastAPI :8000 → RPC Pool → Solana RPC
                                        → TX Parser (preTokenBalances diff)
                                        → Analytics Engine (PnL/WR/Strategy/Score)
                                        → Token Resolver (DexScreener)
                                        → In-memory Cache (24h TTL)
                                        → SQLite Analytics Collector
```

**Key technical decisions:**
- **No Helius dependency** — uses free Alchemy RPC + public Solana fallback
- **100% transaction fetch rate** — 2-pass retry with concurrent + sequential fallback
- **FIFO cost basis** — accurate PnL for partial sells and multiple buys
- **preTokenBalances diff** — handles WSOL wrapping, token creation in same tx
- **Max 3 concurrent scans** — semaphore prevents server overload

## Performance

| Metric | Value |
|---|---|
| Fetch rate | 100% (zero lost transactions) |
| Scan time (500 tx) | ~15-30 seconds |
| Scan time (1000 tx) | ~30-60 seconds |
| Cached response | < 50ms |
| Throughput (cached) | 300+ req/sec |
| Concurrent scans | 3 simultaneous |

## Leaderboard

Community-powered wallet database. Every scan adds to the leaderboard.

```bash
# Top wallets by score
curl https://api.walletintel.dev/leaderboard?sort=score&limit=10

# Top by PnL
curl https://api.walletintel.dev/leaderboard?sort=pnl

# Top by win rate
curl https://api.walletintel.dev/leaderboard?sort=win_rate

# Most scanned
curl https://api.walletintel.dev/leaderboard?sort=popular
```

## Limits

| | Free Tier |
|---|---|
| Price | $0 |
| Requests/day | 10 |
| Max transactions | 5,000 |
| Cache TTL | 24 hours |
| API key required | No |

**Note:** PnL is calculated in SOL, not USD. Default scan depth is 1,000 transactions — use `max_tx=5000` for full history.

## Self-Hosting

```bash
git clone https://github.com/ace8ecar-source/walletintel-v2.git
cd walletintel-v2
pip install -r requirements.txt

# Add your RPC provider
cp .env.example .env
# Edit .env with your Alchemy/other RPC key

python run.py
# API available at http://localhost:8000
```

## Support

- **Telegram:** [@walletintel_daily](https://t.me/walletintel_daily) — daily leaderboard reports
- **GitHub Issues:** [Report bugs & request features](https://github.com/ace8ecar-source/walletintel-v2/issues)
- **Donate:** `ykq4M8KYzbrJ9dwG9mAcRuQope1arSWVJzyDRJX4MFj` (any Solana token accepted)

## Keywords

Solana wallet analyzer, Solana PnL API, Solana wallet PnL, free Solana API, Solana win rate, Solana trading bot API, Solana copy trade, Solana smart money, Solana wallet score, pump.fun wallet analyzer, Solana DeFi analytics, Solana wallet tracker, Solana trading analytics, Solana wallet intelligence

---

**Built with ⚡ on Solana** | [walletintel.dev](https://walletintel.dev)
