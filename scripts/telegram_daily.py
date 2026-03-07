#!/usr/bin/env python3
"""
WalletIntel v2 — Telegram Daily Report Bot

Posts daily leaderboard and stats to @walletintel_daily channel.
Run via cron every 24 hours.

Usage:
    python3 scripts/telegram_daily.py
    
Cron (every day at 10:00 UTC):
    0 10 * * * cd /home/walletintel/walletintel-v2 && /home/walletintel/walletintel-v2/venv/bin/python scripts/telegram_daily.py >> logs/telegram.log 2>&1

Env vars needed:
    TELEGRAM_BOT_TOKEN - from @BotFather
    TELEGRAM_CHANNEL_ID - @walletintel_daily
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# Config
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@walletintel_daily")
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")


def api_get(endpoint: str) -> dict:
    """Get data from WalletIntel API."""
    url = f"{API_BASE}{endpoint}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"API error: {e}")
        return {}


def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Send message to Telegram channel."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"Message sent to {CHANNEL_ID}")
                return True
            else:
                print(f"Telegram error: {result}")
                return False
    except Exception as e:
        print(f"Telegram send error: {e}")
        return False


def build_daily_report() -> str:
    """Build daily leaderboard report."""
    # Get stats
    stats = api_get("/stats")
    usage = stats.get("usage", {})

    # Get leaderboard by different criteria
    top_score = api_get("/leaderboard?sort=score&limit=5")
    top_pnl = api_get("/leaderboard?sort=pnl&limit=5")

    today = time.strftime("%B %d, %Y", time.gmtime())

    # Build message
    msg = f"🏆 <b>WalletIntel Daily Report</b>\n"
    msg += f"📅 {today}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n\n"

    # Stats
    total_wallets = usage.get("unique_wallets_scanned", 0)
    total_scans = usage.get("total_scans", 0)
    avg_score = usage.get("avg_wallet_score", 0)

    msg += f"📊 <b>Platform Stats</b>\n"
    msg += f"   Wallets analyzed: <b>{total_wallets}</b>\n"
    msg += f"   Total scans: <b>{total_scans}</b>\n"
    msg += f"   Avg wallet score: <b>{avg_score}</b>/100\n\n"

    # Top by Score
    wallets = top_score.get("wallets", [])
    if wallets:
        msg += f"⭐ <b>Top 5 by Score</b>\n"
        for i, w in enumerate(wallets, 1):
            addr = w["wallet"][:6] + "..." + w["wallet"][-4:]
            pnl = w["pnl_sol"]
            pnl_sign = "+" if pnl >= 0 else ""
            strategy = w["strategy"].replace("_", " ").title()
            msg += f"   {i}. <code>{addr}</code> "
            msg += f"Score:<b>{w['score']}</b> "
            msg += f"WR:{w['win_rate']}% "
            msg += f"PnL:{pnl_sign}{pnl:.1f}SOL "
            msg += f"[{strategy}]\n"
        msg += "\n"

    # Top by PnL
    wallets_pnl = top_pnl.get("wallets", [])
    if wallets_pnl:
        msg += f"💰 <b>Top 5 by PnL</b>\n"
        for i, w in enumerate(wallets_pnl, 1):
            addr = w["wallet"][:6] + "..." + w["wallet"][-4:]
            pnl = w["pnl_sol"]
            msg += f"   {i}. <code>{addr}</code> "
            msg += f"+{pnl:.1f} SOL "
            msg += f"(Score:{w['score']} WR:{w['win_rate']}%)\n"
        msg += "\n"

    # Footer
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🔍 Scan any wallet free: <a href=\"https://walletintel.dev\">walletintel.dev</a>\n"
    msg += f"📖 API Docs: <a href=\"https://walletintel.dev/docs\">walletintel.dev/docs</a>\n"
    msg += f"💻 Open Source: <a href=\"https://github.com/ace8ecar-source/walletintel-v2\">GitHub</a>"

    return msg


def build_new_whales_alert() -> str:
    """Build alert for newly discovered high-score wallets."""
    # This would compare today's scans with yesterday's
    # For now, just highlight any score 90+ wallets
    top = api_get("/leaderboard?sort=score&limit=3&min_trades=10")
    wallets = top.get("wallets", [])

    if not wallets:
        return ""

    msg = f"🐋 <b>Smart Money Spotlight</b>\n\n"

    for w in wallets:
        addr = w["wallet"][:6] + "..." + w["wallet"][-4:]
        pnl = w["pnl_sol"]
        pnl_sign = "+" if pnl >= 0 else ""
        strategy = w["strategy"].replace("_", " ").title()

        msg += f"<code>{w['wallet']}</code>\n"
        msg += f"   Score: <b>{w['score']}/100</b> | "
        msg += f"WR: <b>{w['win_rate']}%</b> | "
        msg += f"PnL: <b>{pnl_sign}{pnl:.1f} SOL</b>\n"
        msg += f"   Strategy: {strategy} | "
        msg += f"Trades: {w['total_trades']} | "
        msg += f"Tokens: {w['unique_tokens']}\n"
        msg += f"   🔗 <a href=\"https://api.walletintel.dev/wallet/{w['wallet']}/pnl\">Full Analysis</a>\n\n"

    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📡 Powered by <a href=\"https://walletintel.dev\">WalletIntel</a>"

    return msg


def main():
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set")
        print("Set it in .env or export TELEGRAM_BOT_TOKEN=your_token")
        sys.exit(1)

    print(f"Building daily report... ({time.strftime('%Y-%m-%d %H:%M:%S UTC')})")

    # Send daily leaderboard
    report = build_daily_report()
    if report:
        print(f"Report length: {len(report)} chars")
        success = send_telegram(report)
        if not success:
            sys.exit(1)
    else:
        print("Failed to build report")
        sys.exit(1)

    # Wait a moment between messages
    time.sleep(2)

    # Send whale spotlight
    whale_alert = build_new_whales_alert()
    if whale_alert:
        print(f"Whale alert length: {len(whale_alert)} chars")
        send_telegram(whale_alert)

    print("Done!")


if __name__ == "__main__":
    main()
