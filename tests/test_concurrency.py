"""Race test: many threads spend from one account at the same time.

If two transfers could both read the same balance before either wrote it, the
account would be overdrawn or money would appear from nowhere. The ledger must
balance exactly afterwards.
"""
import shutil
import threading
import uuid

import pytest

from app import db
from app.models import BankError
from app.repositories import AccountRepository, Database
from app.services import TransferService


@pytest.mark.parametrize("tuned", [True, False])
def test_parallel_transfers_never_overdraw(bank_db, tmp_path, tuned):
    path = tmp_path / "race.db"
    shutil.copy(bank_db[0], path)
    database = Database(path, tuned=tuned)
    accounts = AccountRepository(database)
    service = TransferService(database, accounts)
    conn = db.connect(path)
    total_before = conn.execute("SELECT SUM(balance) FROM accounts").fetchone()[0]
    src = accounts.for_customer(1)[0]
    dst = accounts.for_customer(2)[0]
    # 8 threads x 25 transfers, each for 1/100 of the balance + 1 rupee: some must fail
    amount_paise = src.balance // 100 + 100
    amount = f"{amount_paise // 100}.{amount_paise % 100:02d}"
    ok, failed, errors = [], [], []

    def worker():
        for _ in range(25):
            try:
                service.transfer(1, src.id, dst.number, amount, "race", uuid.uuid4().hex)
                ok.append(1)
            except BankError:
                failed.append(1)
            except Exception as e:          # pragma: no cover - surfaced below
                errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    src_after = accounts.get(src.id).balance
    assert src_after >= 0
    assert src_after == src.balance - len(ok) * amount_paise
    assert len(ok) == src.balance // amount_paise            # exactly as many as could be afforded
    assert len(ok) + len(failed) == 200
    total_after = conn.execute("SELECT SUM(balance) FROM accounts").fetchone()[0]
    assert total_after == total_before                       # money is neither made nor lost
    last = conn.execute("SELECT balance_after FROM transactions WHERE account_id = ? "
                        "ORDER BY ts DESC, id DESC LIMIT 1", (src.id,)).fetchone()[0]
    assert last == src_after                                 # ledger matches balance
