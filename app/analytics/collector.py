"""
WalletIntel v2 — Analytics Collector

SQLite-based tracking of API usage and scanned wallets.
Builds a wallet intelligence database from user scans.
"""
import sqlite3
import time
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "analytics.db"


class AnalyticsCollector:
    """
    Collects scan data into SQLite.

    Tracks:
    - Daily request counts
    - Scanned wallets with their scores/PnL/WR
    - Top wallets leaderboard
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    total_requests INTEGER DEFAULT 0,
                    unique_ips INTEGER DEFAULT 0,
                    unique_wallets INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS scan_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    wallet TEXT,
                    score INTEGER,
                    win_rate REAL,
                    pnl_sol REAL,
                    strategy TEXT,
                    total_trades INTEGER,
                    unique_tokens INTEGER,
                    scan_time REAL
                );

                CREATE TABLE IF NOT EXISTS wallet_scores (
                    wallet TEXT PRIMARY KEY,
                    score INTEGER,
                    win_rate REAL,
                    pnl_sol REAL,
                    strategy TEXT,
                    total_trades INTEGER,
                    unique_tokens INTEGER,
                    scan_count INTEGER DEFAULT 1,
                    first_seen REAL,
                    last_seen REAL
                );

                CREATE TABLE IF NOT EXISTS request_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    date TEXT,
                    ip_hash TEXT,
                    endpoint TEXT,
                    wallet TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_wallet_scores_score
                    ON wallet_scores(score DESC);
                CREATE INDEX IF NOT EXISTS idx_wallet_scores_pnl
                    ON wallet_scores(pnl_sol DESC);
                CREATE INDEX IF NOT EXISTS idx_wallet_scores_wr
                    ON wallet_scores(win_rate DESC);
                CREATE INDEX IF NOT EXISTS idx_request_log_date
                    ON request_log(date);
            """)
        logger.info(f"Analytics DB initialized at {self.db_path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def log_request(self, ip_hash: str, endpoint: str, wallet: str = ""):
        """Log every API request (lightweight)."""
        now = time.time()
        date = time.strftime("%Y-%m-%d", time.gmtime(now))
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO request_log (timestamp, date, ip_hash, endpoint, wallet) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (now, date, ip_hash, endpoint, wallet)
                )
        except Exception as e:
            logger.debug(f"Failed to log request: {e}")

    def log_scan(self, wallet: str, score: int, win_rate: float,
                 pnl_sol: float, strategy: str, total_trades: int,
                 unique_tokens: int, scan_time: float):
        """Log a completed wallet scan with results."""
        now = time.time()
        try:
            with self._lock, self._connect() as conn:
                # Log individual scan
                conn.execute(
                    "INSERT INTO scan_log "
                    "(timestamp, wallet, score, win_rate, pnl_sol, strategy, "
                    "total_trades, unique_tokens, scan_time) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, wallet, score, win_rate, pnl_sol, strategy,
                     total_trades, unique_tokens, scan_time)
                )

                # Upsert wallet scores (keep best data)
                existing = conn.execute(
                    "SELECT scan_count FROM wallet_scores WHERE wallet = ?",
                    (wallet,)
                ).fetchone()

                if existing:
                    conn.execute(
                        "UPDATE wallet_scores SET "
                        "score=?, win_rate=?, pnl_sol=?, strategy=?, "
                        "total_trades=?, unique_tokens=?, "
                        "scan_count=scan_count+1, last_seen=? "
                        "WHERE wallet=?",
                        (score, win_rate, pnl_sol, strategy,
                         total_trades, unique_tokens, now, wallet)
                    )
                else:
                    conn.execute(
                        "INSERT INTO wallet_scores "
                        "(wallet, score, win_rate, pnl_sol, strategy, "
                        "total_trades, unique_tokens, scan_count, "
                        "first_seen, last_seen) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                        (wallet, score, win_rate, pnl_sol, strategy,
                         total_trades, unique_tokens, now, now)
                    )
        except Exception as e:
            logger.debug(f"Failed to log scan: {e}")

    def get_top_wallets(self, sort_by: str = "score", limit: int = 20,
                        min_trades: int = 5) -> List[Dict]:
        """Get top wallets leaderboard."""
        allowed_sorts = {
            "score": "score DESC",
            "pnl": "pnl_sol DESC",
            "win_rate": "win_rate DESC",
            "trades": "total_trades DESC",
            "popular": "scan_count DESC",
        }
        order = allowed_sorts.get(sort_by, "score DESC")

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT wallet, score, win_rate, pnl_sol, strategy, "
                    f"total_trades, unique_tokens, scan_count, last_seen "
                    f"FROM wallet_scores "
                    f"WHERE total_trades >= ? "
                    f"ORDER BY {order} "
                    f"LIMIT ?",
                    (min_trades, limit)
                ).fetchall()

                return [
                    {
                        "wallet": r["wallet"],
                        "score": r["score"],
                        "win_rate": r["win_rate"],
                        "pnl_sol": round(r["pnl_sol"], 4),
                        "strategy": r["strategy"],
                        "total_trades": r["total_trades"],
                        "unique_tokens": r["unique_tokens"],
                        "scan_count": r["scan_count"],
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.debug(f"Failed to get top wallets: {e}")
            return []

    def get_usage_stats(self) -> Dict:
        """Get overall usage statistics."""
        try:
            with self._connect() as conn:
                total_scans = conn.execute(
                    "SELECT COUNT(*) as c FROM scan_log"
                ).fetchone()["c"]

                unique_wallets = conn.execute(
                    "SELECT COUNT(*) as c FROM wallet_scores"
                ).fetchone()["c"]

                today = time.strftime("%Y-%m-%d", time.gmtime())
                today_requests = conn.execute(
                    "SELECT COUNT(*) as c FROM request_log WHERE date = ?",
                    (today,)
                ).fetchone()["c"]

                today_unique_ips = conn.execute(
                    "SELECT COUNT(DISTINCT ip_hash) as c FROM request_log WHERE date = ?",
                    (today,)
                ).fetchone()["c"]

                avg_score = conn.execute(
                    "SELECT AVG(score) as a FROM wallet_scores WHERE total_trades >= 5"
                ).fetchone()["a"]

                return {
                    "total_scans": total_scans,
                    "unique_wallets_scanned": unique_wallets,
                    "today_requests": today_requests,
                    "today_unique_visitors": today_unique_ips,
                    "avg_wallet_score": round(avg_score or 0, 1),
                }
        except Exception as e:
            logger.debug(f"Failed to get usage stats: {e}")
            return {}
