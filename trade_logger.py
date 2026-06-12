"""SQLite トレードログ"""

import sqlite3
import logging
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at    TEXT    NOT NULL,
                closed_at    TEXT,
                symbol       TEXT    NOT NULL,
                direction    TEXT    NOT NULL,
                entry_price  REAL,
                exit_price   REAL,
                lot_size     REAL,
                sl_price     REAL,
                tp_price     REAL,
                ta_rating    TEXT,
                ta_direction TEXT,
                ta_reasoning TEXT,
                result_pips   REAL,
                result_profit REAL,
                mt5_ticket   INTEGER,
                status       TEXT DEFAULT 'OPEN',
                exit_reason  TEXT
            );

            CREATE TABLE IF NOT EXISTS ta_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                yf_ticker   TEXT,
                direction   TEXT,
                rating      TEXT,
                reasoning   TEXT,
                analysts    TEXT
            );

            CREATE TABLE IF NOT EXISTS heartbeats (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                status    TEXT    NOT NULL,
                details   TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_trades_status    ON trades(status);
            CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);
            CREATE INDEX IF NOT EXISTS idx_trades_ticket    ON trades(mt5_ticket);
            CREATE INDEX IF NOT EXISTS idx_ta_logs_ts       ON ta_logs(timestamp);
        """)
    logger.info("DB initialized: %s", config.DB_PATH)


# ── trades ──────────────────────────────

def insert_trade(symbol: str, direction: str, entry_price: float,
                 lot_size: float, sl_price: float, tp_price: float | None,
                 ta_rating: str, ta_direction: str, ta_reasoning: str,
                 mt5_ticket: int) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO trades
               (opened_at, symbol, direction, entry_price, lot_size, sl_price, tp_price,
                ta_rating, ta_direction, ta_reasoning, mt5_ticket, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')""",
            (datetime.utcnow().isoformat(), symbol, direction,
             entry_price, lot_size, sl_price, tp_price,
             ta_rating, ta_direction, ta_reasoning[:1000], mt5_ticket),
        )
        return cur.lastrowid


def close_trade(trade_id: int, exit_price: float, result_pips: float,
                result_profit: float, exit_reason: str | None = None):
    with _conn() as c:
        c.execute(
            """UPDATE trades
               SET closed_at=?, exit_price=?, result_pips=?, result_profit=?,
                   status='CLOSED', exit_reason=?
               WHERE id=?""",
            (datetime.utcnow().isoformat(), exit_price,
             result_pips, result_profit, exit_reason, trade_id),
        )


def get_open_trades() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM trades WHERE status='OPEN' ORDER BY opened_at"
        ).fetchall()
    return [dict(r) for r in rows]


def get_trade_by_ticket(ticket: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM trades WHERE mt5_ticket=? AND status='OPEN'", (ticket,)
        ).fetchone()
    return dict(row) if row else None


def get_last_closed_trade(symbol: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM trades WHERE symbol LIKE ? AND status='CLOSED' "
            "ORDER BY closed_at DESC LIMIT 1",
            (f"%{symbol.rstrip('#.')}%",),
        ).fetchone()
    return dict(row) if row else None


# ── ta_logs ──────────────────────────────

def insert_ta_log(symbol: str, yf_ticker: str, direction: str,
                  rating: str, reasoning: str, analysts: list[str]):
    with _conn() as c:
        c.execute(
            """INSERT INTO ta_logs
               (timestamp, symbol, yf_ticker, direction, rating, reasoning, analysts)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (datetime.utcnow().isoformat(), symbol, yf_ticker,
             direction, rating, reasoning[:2000], ",".join(analysts)),
        )


# ── メンテナンス ──────────────────────────

def run_maintenance(full_vacuum: bool = False):
    now = datetime.utcnow()
    with _conn() as c:
        c.execute("DELETE FROM ta_logs WHERE timestamp < ?",
                  ((now - timedelta(days=config.DB_RETENTION_DAYS_AI_LOGS)).isoformat(),))
        c.execute("DELETE FROM heartbeats WHERE timestamp < ?",
                  ((now - timedelta(days=config.DB_RETENTION_DAYS_HEARTBEATS)).isoformat(),))
        c.execute("DELETE FROM trades WHERE status='CLOSED' AND closed_at < ?",
                  ((now - timedelta(days=config.DB_RETENTION_DAYS_CLOSED_TRADES)).isoformat(),))

        # 行数上限
        c.execute("""DELETE FROM ta_logs WHERE id NOT IN
                     (SELECT id FROM ta_logs ORDER BY id DESC LIMIT ?)""",
                  (config.DB_MAX_AI_LOG_ROWS,))
        c.execute("""DELETE FROM heartbeats WHERE id NOT IN
                     (SELECT id FROM heartbeats ORDER BY id DESC LIMIT ?)""",
                  (config.DB_MAX_HEARTBEAT_ROWS,))

        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    if full_vacuum:
        with _conn() as c:
            c.execute("VACUUM")

    logger.info("DB maintenance done (vacuum=%s)", full_vacuum)
