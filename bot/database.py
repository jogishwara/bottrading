"""SQLite database module for the crypto trading bot."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "bot.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Create users table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        # Create user config table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_config (
                user_id INTEGER PRIMARY KEY,
                api_key TEXT DEFAULT '',
                api_secret TEXT DEFAULT '',
                telegram_token TEXT DEFAULT '',
                telegram_chat_id TEXT DEFAULT '',
                mode TEXT DEFAULT 'paper',
                exchange_env TEXT DEFAULT 'demo',
                symbol TEXT DEFAULT 'SOL/USDT:USDT',
                timeframe TEXT DEFAULT '1m',
                fast_ema INTEGER DEFAULT 9,
                slow_ema INTEGER DEFAULT 21,
                leverage INTEGER DEFAULT 2,
                risk_per_trade REAL DEFAULT 0.01,
                trade_margin_usdt REAL DEFAULT 10.0,
                max_daily_loss REAL DEFAULT 0.03,
                stop_loss_pct REAL DEFAULT 0.01,
                take_profit_pct REAL DEFAULT 0.02,
                cooldown_seconds INTEGER DEFAULT 300,
                paper_initial_balance REAL DEFAULT 1000.0,
                enable_websocket INTEGER DEFAULT 0,
                is_running INTEGER DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        # Create sessions table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        # Create trades table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                amount REAL NOT NULL,
                pnl REAL NOT NULL,
                reason TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hash_bytes = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000,
    )
    return f"{salt}:{hash_bytes.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, hash_hex = hashed.split(":")
        hash_bytes = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000,
        )
        return hash_bytes.hex() == hash_hex
    except Exception:
        return False


def register_user(username: str, password_raw: str) -> int | None:
    pw_hash = hash_password(password_raw)
    now_str = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username.lower().strip(), pw_hash, now_str),
            )
            user_id = cursor.lastrowid
            
            # Create default config for this user
            cursor.execute(
                "INSERT INTO user_config (user_id) VALUES (?)",
                (user_id,),
            )
            conn.commit()
            return user_id
    except sqlite3.IntegrityError:
        return None


def authenticate_user(username: str, password_raw: str) -> dict[str, Any] | None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username.lower().strip(),))
        row = cursor.fetchone()
        if row and verify_password(password_raw, row["password_hash"]):
            return {"id": row["id"], "username": row["username"]}
    return None


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires),
        )
        conn.commit()
    return token


def get_user_by_session(token: str) -> dict[str, Any] | None:
    now_str = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.id, u.username 
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, now_str),
        )
        row = cursor.fetchone()
        if row:
            return {"id": row["id"], "username": row["username"]}
    return None


def delete_session(token: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


def get_user_config(user_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_config WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def save_user_config(user_id: int, updates: dict[str, Any]) -> None:
    # Build dynamic update statement
    fields = []
    values = []
    
    # Define whitelist of updateable fields
    allowed_fields = {
        "api_key", "api_secret", "telegram_token", "telegram_chat_id",
        "mode", "exchange_env", "symbol", "timeframe", "fast_ema", "slow_ema",
        "leverage", "risk_per_trade", "trade_margin_usdt", "max_daily_loss",
        "stop_loss_pct", "take_profit_pct", "cooldown_seconds",
        "paper_initial_balance", "enable_websocket"
    }

    for key, value in updates.items():
        if key in allowed_fields:
            fields.append(f"{key} = ?")
            values.append(value)
            
    if not fields:
        return
        
    values.append(user_id)
    query = f"UPDATE user_config SET {', '.join(fields)} WHERE user_id = ?"
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, tuple(values))
        conn.commit()


def set_bot_running_status(user_id: int, is_running: bool) -> None:
    val = 1 if is_running else 0
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE user_config SET is_running = ? WHERE user_id = ?", (val, user_id))
        conn.commit()


def get_running_user_ids() -> list[int]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM user_config WHERE is_running = 1")
        rows = cursor.fetchall()
        return [row["user_id"] for row in rows]


def record_user_trade(
    user_id: int,
    side: str,
    entry_price: float,
    exit_price: float,
    amount: float,
    pnl: float,
    reason: str,
    opened_at: str,
    closed_at: str,
) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO trades (user_id, side, entry_price, exit_price, amount, pnl, reason, opened_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, side, entry_price, exit_price, amount, pnl, reason, opened_at, closed_at),
        )
        conn.commit()


def get_user_trades(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM trades WHERE user_id = ? ORDER BY closed_at DESC LIMIT ?",
            (user_id, limit),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


# Initialize on import
init_db()
