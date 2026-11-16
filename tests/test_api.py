"""API tests. Every test runs against both the tuned and the baseline
data-access layer, so tuning can never change behaviour."""
import uuid

from conftest import login


def accounts(client, h):
    return client.get("/api/accounts", headers=h).json()


def other_account_number(client, customer_id=2):
    h = login(client, customer_id)
    return accounts(client, h)[0]["number"]


def send(client, h, src, to, amount, key=None, desc="Test"):
    return client.post("/api/transfers", headers=h, json={
        "from_account_id": src, "to_account_number": to, "amount": amount,
        "description": desc, "idempotency_key": key or uuid.uuid4().hex})


def test_login_and_wrong_password(client):
    assert client.post("/api/login", json={"customer_id": 1, "password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"customer_id": 99999, "password": "x"}).status_code == 401
    login(client)


def test_endpoints_need_login(client):
    assert client.get("/api/accounts").status_code == 401
    assert client.get("/api/accounts/1/statement").status_code == 401


def test_cannot_read_someone_elses_statement(client):
    h = login(client, 1)
    theirs = accounts(client, login(client, 2))[0]["id"]
    assert client.get(f"/api/accounts/{theirs}/statement", headers=h).status_code == 404


def test_transfer_moves_money_both_sides(client):
    h1 = login(client, 1)
    src = accounts(client, h1)[0]
    h2 = login(client, 2)
    dst = accounts(client, h2)[0]
    r = send(client, h1, src["id"], dst["number"], "1234.56")
    assert r.status_code == 200, r.text
    assert accounts(client, h1)[0]["balance"] == src["balance"] - 123456
    assert accounts(client, h2)[0]["balance"] == dst["balance"] + 123456
    top = client.get(f"/api/accounts/{src['id']}/statement?limit=1", headers=h1).json()
    t = top["transactions"][0]
    assert t["amount"] == -123456 and t["counterparty"] == dst["number"]
    assert t["balance_after"] == src["balance"] - 123456


def test_insufficient_funds_changes_nothing(client):
    # pick an account whose balance is below the per-transfer limit
    for cid in range(1, 40):
        h = login(client, cid)
        src = next((a for a in accounts(client, h) if a["balance"] < 100_000_000 - 100), None)
        if src:
            break
    to = other_account_number(client, 2 if cid != 2 else 3)
    r = send(client, h, src["id"], to, str(src["balance"] // 100 + 1))
    assert r.status_code == 422 and r.json()["error"] == "Insufficient funds"
    assert next(a for a in accounts(client, h) if a["id"] == src["id"])["balance"] == src["balance"]


def test_bad_transfers_rejected(client):
    h = login(client, 1)
    src = accounts(client, h)[0]
    to = other_account_number(client)
    assert send(client, h, src["id"], src["number"], "10").status_code == 422   # same account
    assert send(client, h, src["id"], "000", "10").status_code == 404           # unknown payee
    assert send(client, h, src["id"], to, "-10").status_code == 422
    assert send(client, h, src["id"], to, "10.001").status_code == 422
    theirs = accounts(client, login(client, 2))[0]["id"]
    assert send(client, h, theirs, to, "10").status_code == 404                 # not my account


def test_idempotent_retry_charges_once(client):
    h = login(client, 1)
    src = accounts(client, h)[0]
    key = uuid.uuid4().hex
    to = other_account_number(client)
    a = send(client, h, src["id"], to, "100", key).json()
    b = send(client, h, src["id"], to, "100", key).json()
    assert a["transfer_id"] == b["transfer_id"] and b["duplicate"] is True
    assert accounts(client, h)[0]["balance"] == src["balance"] - 10000


def test_pagination_has_no_gaps_or_duplicates(client):
    h = login(client, 1)
    acct = accounts(client, h)[0]["id"]
    seen, cursor = [], None
    while True:
        url = f"/api/accounts/{acct}/statement?limit=37" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=h).json()
        seen += [t["id"] for t in page["transactions"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    full = client.get(f"/api/accounts/{acct}/statement?limit=100", headers=h).json()
    assert len(seen) == len(set(seen))
    assert seen[:len(full["transactions"])] == [t["id"] for t in full["transactions"]]


def test_search_is_case_insensitive_and_literal(client):
    h = login(client, 1)
    acct = accounts(client, h)[0]["id"]
    rows = client.get(f"/api/accounts/{acct}/statement?q=SALARY&limit=100", headers=h).json()
    assert all("salary" in t["description"].lower() for t in rows["transactions"])
    none = client.get(f"/api/accounts/{acct}/statement?q=%25%25&limit=100", headers=h).json()
    assert none["transactions"] == []                     # '%' is not a wildcard
