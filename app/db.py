from __future__ import annotations

import base64
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable


META_SALT_KEY = "kdf_salt"


def db_path() -> Path:
    base_dir = Path(__file__).resolve().parents[1]
    default_path = base_dir / "data" / "order_status.db"
    return Path(os.environ.get("ORDER_STATUS_DB_PATH", str(default_path)))


def get_conn() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    if _needs_credentials_migration(conn):
        _migrate_credentials(conn)
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                label TEXT NOT NULL,
                api_key_enc TEXT NOT NULL,
                api_secret_enc TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(exchange, label)
            )
            """
        )
    conn.commit()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _needs_credentials_migration(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "credentials"):
        return False
    indexes = conn.execute("PRAGMA index_list('credentials')").fetchall()
    for idx in indexes:
        if not idx["unique"]:
            continue
        columns = conn.execute(
            f"PRAGMA index_info('{idx['name']}')"
        ).fetchall()
        col_names = [col["name"] for col in columns]
        if col_names == ["exchange"]:
            return True
    return False


def _migrate_credentials(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE credentials RENAME TO credentials_old")
    conn.execute(
        """
        CREATE TABLE credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            label TEXT NOT NULL,
            api_key_enc TEXT NOT NULL,
            api_secret_enc TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(exchange, label)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO credentials (
            id,
            exchange,
            label,
            api_key_enc,
            api_secret_enc,
            created_at,
            updated_at
        )
        SELECT
            id,
            exchange,
            CASE
                WHEN label IS NULL OR length(trim(label)) = 0 THEN 'default'
                ELSE label
            END AS label,
            api_key_enc,
            api_secret_enc,
            created_at,
            updated_at
        FROM credentials_old
        """
    )
    conn.execute("DROP TABLE credentials_old")


def ensure_kdf_salt(conn: sqlite3.Connection) -> bytes:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_SALT_KEY,)).fetchone()
    if row:
        return base64.urlsafe_b64decode(row["value"].encode("ascii"))

    salt = os.urandom(16)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        (META_SALT_KEY, base64.urlsafe_b64encode(salt).decode("ascii")),
    )
    conn.commit()
    return salt


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def upsert_credentials(
    conn: sqlite3.Connection,
    exchange: str,
    label: str,
    api_key_enc: str,
    api_secret_enc: str,
) -> None:
    now = utc_now()
    existing = conn.execute(
        "SELECT id FROM credentials WHERE exchange = ? AND label = ?",
        (exchange, label),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE credentials
            SET label = ?, api_key_enc = ?, api_secret_enc = ?, updated_at = ?
            WHERE exchange = ? AND label = ?
            """,
            (label, api_key_enc, api_secret_enc, now, exchange, label),
        )
    else:
        conn.execute(
            """
            INSERT INTO credentials (exchange, label, api_key_enc, api_secret_enc, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (exchange, label, api_key_enc, api_secret_enc, now, now),
        )
    conn.commit()


def get_credentials(
    conn: sqlite3.Connection, exchange: str, label: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM credentials WHERE exchange = ? AND label = ?",
        (exchange, label),
    ).fetchone()


def list_credentials(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM credentials ORDER BY exchange ASC, label ASC"
    ).fetchall()
