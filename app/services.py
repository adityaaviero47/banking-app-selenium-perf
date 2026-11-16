"""Business logic: authentication and fund transfers."""
import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timezone

from .models import (AuthError, InsufficientFunds, InvalidTransfer, NotFound,
                     parse_amount)
from .repositories import AccountRepository, Database

PBKDF2_ROUNDS = 60_000


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                               PBKDF2_ROUNDS).hex()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class AuthService:
    """Password check plus in-memory session tokens."""

    def __init__(self, database: Database):
        self.db = database
        self._sessions = {}
        self._lock = threading.Lock()

    def login(self, customer_id, password):
        with self.db.connection() as c:
            r = c.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if r is None or not hmac.compare_digest(hash_password(password, r["salt"]),
                                                r["password_hash"]):
            raise AuthError("Wrong customer ID or password")
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._sessions[token] = r["id"]
        return token, r["name"]

    def customer_for(self, token):
        with self._lock:
            cid = self._sessions.get(token or "")
        if cid is None:
            raise AuthError("Please log in")
        return cid

    def logout(self, token):
        with self._lock:
            self._sessions.pop(token or "", None)


class TransferService:
    def __init__(self, database: Database, accounts: AccountRepository):
        self.db, self.accounts = database, accounts

    def transfer(self, customer_id, from_account_id, to_number, amount_text,
                 description, idempotency_key):
        amount = parse_amount(amount_text)
        if not idempotency_key or len(idempotency_key) > 64:
            raise InvalidTransfer("Missing idempotency key")
        description = (description or "Transfer").strip()[:80] or "Transfer"
        with self.db.write_transaction() as c:
            done = c.execute("SELECT * FROM transfers WHERE idempotency_key = ?",
                             (idempotency_key,)).fetchone()
            if done is not None:                       # retried or double-clicked
                return {"transfer_id": done["id"], "duplicate": True}
            src = self.accounts.get(from_account_id, conn=c)
            if src.customer_id != customer_id:
                raise NotFound("Account not found")
            dst = self.accounts.by_number(to_number.strip(), c)
            if dst.id == src.id:
                raise InvalidTransfer("Cannot transfer to the same account")
            if src.balance < amount:
                raise InsufficientFunds("Insufficient funds")
            ts = now_iso()
            c.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?", (amount, src.id))
            c.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (amount, dst.id))
            c.execute("INSERT INTO transactions (account_id, counterparty_account, amount, "
                      "balance_after, description, ts) VALUES (?, ?, ?, ?, ?, ?)",
                      (src.id, dst.id, -amount, src.balance - amount, description, ts))
            c.execute("INSERT INTO transactions (account_id, counterparty_account, amount, "
                      "balance_after, description, ts) VALUES (?, ?, ?, ?, ?, ?)",
                      (dst.id, src.id, amount, dst.balance + amount, description, ts))
            cur = c.execute("INSERT INTO transfers (idempotency_key, from_account, to_account, "
                            "amount, ts) VALUES (?, ?, ?, ?, ?)",
                            (idempotency_key, src.id, dst.id, amount, ts))
            return {"transfer_id": cur.lastrowid, "duplicate": False,
                    "balance": src.balance - amount}
