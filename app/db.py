"""SQLite schema, connections and tuning switches.

Money is stored as integer paise (1 INR = 100 paise) so balances never pick up
floating-point rounding errors.
"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    number       TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL CHECK (kind IN ('SAVINGS', 'CURRENT')),
    balance      INTEGER NOT NULL CHECK (balance >= 0)
);
CREATE TABLE IF NOT EXISTS transactions (
    id                   INTEGER PRIMARY KEY,
    account_id           INTEGER NOT NULL REFERENCES accounts(id),
    counterparty_account INTEGER REFERENCES accounts(id),
    amount               INTEGER NOT NULL,          -- signed: credit > 0, debit < 0
    balance_after        INTEGER NOT NULL,
    description          TEXT NOT NULL,
    ts                   TEXT NOT NULL              -- ISO-8601 UTC
);
CREATE TABLE IF NOT EXISTS transfers (
    id              INTEGER PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    from_account    INTEGER NOT NULL,
    to_account      INTEGER NOT NULL,
    amount          INTEGER NOT NULL,
    ts              TEXT NOT NULL
);
"""

# The index the tuned statement query relies on (added by `apply_tuning`).
STATEMENT_INDEX = ("CREATE INDEX IF NOT EXISTS ix_txn_account_ts "
                   "ON transactions(account_id, ts DESC, id DESC)")


def connect(path, tuned=True):
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if tuned:
        conn.execute("PRAGMA journal_mode = WAL")       # readers never block the writer
        conn.execute("PRAGMA synchronous = NORMAL")     # safe with WAL, far fewer fsyncs
        conn.execute("PRAGMA cache_size = -65536")      # 64 MB page cache
    return conn


def create_schema(conn):
    conn.executescript(SCHEMA)


def apply_tuning(conn):
    conn.execute(STATEMENT_INDEX)
    conn.execute("ANALYZE")


def drop_tuning(conn):
    conn.execute("DROP INDEX IF EXISTS ix_txn_account_ts")
    conn.execute("PRAGMA journal_mode = DELETE")


def db_path(default="bank.db"):
    return Path(default)
