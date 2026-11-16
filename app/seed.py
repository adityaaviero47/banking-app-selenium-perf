"""Generate a synthetic bank: customers, accounts and a transaction ledger.

All data is synthetic and seeded, so every run produces the same bank. Most
accounts get a few hundred transactions; a handful of "business" accounts get
tens of thousands, the hot accounts that make statement queries slow.

Usage: python -m app.seed <db_file> [customers] [transactions]
Demo login: any customer ID from 1, password Demo@1234
"""
import random
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .services import hash_password

PASSWORD = "Demo@1234"
DESCRIPTIONS = ["Salary credit", "UPI payment", "Electricity bill", "ATM withdrawal",
                "Card payment - groceries", "Card payment - fuel", "Mobile recharge",
                "Rent", "Insurance premium", "Mutual fund SIP", "Interest credit",
                "NEFT transfer", "IMPS transfer", "Restaurant", "Online shopping"]


def account_number(i):
    return f"5010{i:07d}"


def seed(path, customers=1000, transactions=1_000_000, business_accounts=20, rnd_seed=42):
    rnd = random.Random(rnd_seed)
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = db.connect(path, tuned=True)
    db.create_schema(conn)
    conn.execute("BEGIN")
    accounts = []
    for cid in range(1, customers + 1):
        salt = secrets.token_hex(16)
        conn.execute("INSERT INTO customers VALUES (?, ?, ?, ?)",
                     (cid, f"Customer {cid}", hash_password(PASSWORD, salt), salt))
        for k in range(rnd.choice([1, 1, 2, 2, 3])):
            accounts.append((cid, "SAVINGS" if k == 0 else rnd.choice(["SAVINGS", "CURRENT"])))
    ids = list(range(1, len(accounts) + 1))
    hot = set(rnd.sample(ids, business_accounts))
    weights = [60.0 if a in hot else 1.0 for a in ids]
    counts = [0] * len(ids)
    for a in rnd.choices(ids, weights=weights, k=transactions):
        counts[a - 1] += 1

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    span = 365 * 24 * 3600
    rows = []
    for aid, n in zip(ids, counts):
        balance = rnd.randint(50_000, 5_000_000) * 100          # opening balance, paise
        stamps = sorted(rnd.randrange(span) for _ in range(n))
        for s in stamps:
            amount = rnd.randint(100, 2_500_000)                    # INR 1 - 25,000
            if rnd.random() >= 0.55 and balance >= amount:     # debit only if covered
                amount = -amount
            if rnd.random() < 0.4:
                cp = rnd.randint(1, len(ids))
                cp = None if cp == aid else cp
            else:
                cp = None
            balance += amount
            ts = (start + timedelta(seconds=s, microseconds=rnd.randrange(10**6)))
            rows.append((aid, cp, amount, balance, rnd.choice(DESCRIPTIONS),
                         ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")))
        cid, kind = accounts[aid - 1]
        conn.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                     (aid, cid, account_number(aid), kind, balance))
    conn.executemany("INSERT INTO transactions (account_id, counterparty_account, amount, "
                     "balance_after, description, ts) VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.execute("COMMIT")
    conn.close()
    return {"customers": customers, "accounts": len(ids), "transactions": len(rows),
            "hot_accounts": sorted(hot), "max_txns_per_account": max(counts)}


if __name__ == "__main__":
    args = sys.argv[1:]
    print(seed(args[0], *(int(a) for a in args[1:3])))
