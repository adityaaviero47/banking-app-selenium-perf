"""Data access layer.

Two statement repositories share one interface so their speed can be compared
on the same data:

* `BaselineStatementRepository`: the straightforward first version. OFFSET
  pagination, search filtered in Python, and one extra query per row to look
  up the counterparty (the N+1 pattern).
* `TunedStatementRepository`: keyset pagination on a composite index, search
  in SQL, and a single JOIN for counterparties.
"""
import sqlite3
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager

from . import db
from .models import Account, NotFound, Transaction


class Database:
    """Hands out SQLite connections.

    Tuned mode keeps one connection per thread and reuses it; baseline mode
    opens a fresh connection for every unit of work.
    """

    def __init__(self, path, tuned=True):
        self.path, self.tuned = path, tuned
        self._local = threading.local()

    @contextmanager
    def connection(self):
        if self.tuned:
            conn = getattr(self._local, "conn", None)
            if conn is None:
                conn = self._local.conn = db.connect(self.path, tuned=True)
            yield conn
        else:
            conn = db.connect(self.path, tuned=False)
            try:
                yield conn
            finally:
                conn.close()

    @contextmanager
    def write_transaction(self):
        """BEGIN IMMEDIATE takes the write lock up front, so two transfers can
        never both read the same balance and both spend it."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise


class AccountRepository:
    def __init__(self, database: Database):
        self.db = database

    @staticmethod
    def _row(r):
        return Account(r["id"], r["customer_id"], r["number"], r["kind"], r["balance"])

    def for_customer(self, customer_id):
        with self.db.connection() as c:
            rows = c.execute("SELECT * FROM accounts WHERE customer_id = ? ORDER BY id",
                             (customer_id,)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, account_id, conn=None):
        def q(c):
            return c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if conn is not None:
            r = q(conn)
        else:
            with self.db.connection() as c:
                r = q(c)
        if r is None:
            raise NotFound("Account not found")
        return self._row(r)

    def by_number(self, number, conn):
        r = conn.execute("SELECT * FROM accounts WHERE number = ?", (number,)).fetchone()
        if r is None:
            raise NotFound("No account with that number")
        return self._row(r)


class StatementRepository(ABC):
    def __init__(self, database: Database):
        self.db = database

    @abstractmethod
    def page(self, account_id, limit, cursor=None, query=None):
        """Return (transactions newest first, next_cursor or None)."""


class BaselineStatementRepository(StatementRepository):
    def page(self, account_id, limit, cursor=None, query=None):
        offset = int(cursor or 0)
        with self.db.connection() as c:
            if query:
                rows = c.execute("SELECT * FROM transactions WHERE account_id = ? "
                                 "ORDER BY ts DESC, id DESC", (account_id,)).fetchall()
                rows = [r for r in rows if query.lower() in r["description"].lower()]
                rows = rows[offset:offset + limit + 1]
            else:
                rows = c.execute("SELECT * FROM transactions WHERE account_id = ? "
                                 "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                                 (account_id, limit + 1, offset)).fetchall()
            out = []
            for r in rows[:limit]:
                cp = None
                if r["counterparty_account"] is not None:      # one query per row
                    a = c.execute("SELECT number FROM accounts WHERE id = ?",
                                  (r["counterparty_account"],)).fetchone()
                    cp = a["number"] if a else None
                out.append(Transaction(r["id"], r["amount"], r["balance_after"],
                                       r["description"], r["ts"], cp))
        nxt = str(offset + limit) if len(rows) > limit else None
        return out, nxt


class TunedStatementRepository(StatementRepository):
    SQL = ("SELECT t.id, t.amount, t.balance_after, t.description, t.ts, a.number AS cp "
           "FROM transactions t LEFT JOIN accounts a ON a.id = t.counterparty_account "
           "WHERE t.account_id = ? {keyset} {search} "
           "ORDER BY t.ts DESC, t.id DESC LIMIT ?")

    def page(self, account_id, limit, cursor=None, query=None):
        params = [account_id]
        keyset = search = ""
        if cursor:
            ts, tid = cursor.rsplit("|", 1)
            keyset = "AND (t.ts, t.id) < (?, ?)"
            params += [ts, int(tid)]
        if query:
            search = "AND t.description LIKE ? ESCAPE '\\'"
            esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{esc}%")
        params.append(limit + 1)
        with self.db.connection() as c:
            rows = c.execute(self.SQL.format(keyset=keyset, search=search), params).fetchall()
        out = [Transaction(r["id"], r["amount"], r["balance_after"], r["description"],
                           r["ts"], r["cp"]) for r in rows[:limit]]
        nxt = f"{rows[limit - 1]['ts']}|{rows[limit - 1]['id']}" if len(rows) > limit else None
        return out, nxt
