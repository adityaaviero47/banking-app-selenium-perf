"""End-to-end tests: a real Chrome (headless) drives the real server."""
import sqlite3

import pytest

from app.seed import PASSWORD
from pages import DashboardPage, LoginPage, StatementPage, TransferPage


def db_count(path, sql, *args):
    with sqlite3.connect(path) as c:
        return c.execute(sql, args).fetchone()[0]


def balance(path, number):
    return db_count(path, "SELECT balance FROM accounts WHERE number = ?", number)


def first_number(path, customer_id):
    return db_count(path, "SELECT number FROM accounts WHERE customer_id = ? ORDER BY id LIMIT 1",
                    customer_id)


@pytest.fixture
def signed_in(browser, live_server):
    base, _ = live_server
    browser.delete_all_cookies()
    return LoginPage(browser, base).open().go_to_dashboard(1, PASSWORD)


def test_login_shows_accounts(signed_in, live_server):
    accts = signed_in.accounts()
    assert accts and all(a["number"].startswith("5010") for a in accts)
    assert all(a["balance"].startswith("₹") for a in accts)


def test_wrong_password_shows_error(browser, live_server):
    browser.delete_all_cookies()
    page = LoginPage(browser, live_server[0]).open().login(1, "wrong-password")
    assert page.error() == "Wrong customer ID or password"
    assert browser.current_url == live_server[0] + "/"


@pytest.mark.parametrize("path", ["/dashboard", "/statement?account=1", "/transfer"])
def test_pages_redirect_when_logged_out(browser, live_server, path):
    browser.delete_all_cookies()
    browser.get(live_server[0] + path)
    LoginPage(browser, live_server[0]).el("#login-form")
    assert browser.current_url == live_server[0] + "/"


def test_transfer_updates_both_balances(signed_in, browser, live_server):
    base, path = live_server
    src = signed_in.accounts()[0]["number"]
    dst = first_number(path, 2)
    before_src, before_dst = balance(path, src), balance(path, dst)
    page = TransferPage(browser, base).open().ready().fill(dst, "250.75", "Rent").submit()
    assert "complete" in page.success()
    assert balance(path, src) == before_src - 25075
    assert balance(path, dst) == before_dst + 25075


def test_insufficient_funds_message(browser, live_server):
    base, path = live_server
    # a customer whose first account holds less than the INR 10 lakh transfer limit
    cid, src = sqlite3.connect(path).execute(
        "SELECT customer_id, number FROM accounts WHERE id IN (SELECT MIN(id) FROM accounts "
        "GROUP BY customer_id) AND balance < 99999900 AND customer_id != 2 ORDER BY customer_id"
    ).fetchone()
    browser.delete_all_cookies()
    LoginPage(browser, base).open().go_to_dashboard(cid, PASSWORD)
    before = balance(path, src)
    page = TransferPage(browser, base).open().ready().fill(first_number(path, 2),
                                                           str(before // 100 + 1))
    assert page.submit().error() == "Insufficient funds"
    assert balance(path, src) == before


@pytest.mark.parametrize("amount,message", [
    ("0", "Amount must be more than zero"),
    ("-50", "Enter an amount like 1500 or 1500.50"),
    ("12.345", "Enter an amount like 1500 or 1500.50"),
    ("2000000", "Amount is above the INR 10,00,000 per-transfer limit"),
])
def test_amount_validation_blocks_request(signed_in, browser, live_server, amount, message):
    base, path = live_server
    before = db_count(path, "SELECT COUNT(*) FROM transfers")
    page = TransferPage(browser, base).open().ready().fill(first_number(path, 2), amount)
    assert page.submit().error() == message
    assert db_count(path, "SELECT COUNT(*) FROM transfers") == before   # nothing sent


def test_same_account_rejected(signed_in, browser, live_server):
    base, _ = live_server
    own = signed_in.accounts()[0]["number"]
    page = TransferPage(browser, base).open().ready().fill(own, "10")
    assert page.submit().error() == "Cannot transfer to the same account"


def test_double_submit_charges_once(signed_in, browser, live_server):
    base, path = live_server
    src = signed_in.accounts()[0]["number"]
    before = balance(path, src)
    n = db_count(path, "SELECT COUNT(*) FROM transfers")
    page = TransferPage(browser, base).open().ready().fill(first_number(path, 2), "99")
    page.double_submit().success()
    page.wait.until(lambda d: db_count(path, "SELECT COUNT(*) FROM transfers") == n + 1)
    assert db_count(path, "SELECT COUNT(*) FROM transfers") == n + 1
    assert balance(path, src) == before - 9900


def test_statement_search_filters_rows(signed_in):
    page = signed_in.open_statement().search("salary")
    descs = page.descriptions()
    assert descs and all("salary" in d.lower() for d in descs)


def test_load_more_appends_without_duplicates(signed_in):
    page = signed_in.open_statement()
    first = page.row_ids()
    ids = page.load_more().row_ids()
    assert len(first) == 25 and len(ids) > 25
    assert ids[:25] == first and len(ids) == len(set(ids))


def test_description_is_shown_as_text_not_html(signed_in, browser, live_server):
    base, path = live_server
    evil = '<img src=x onerror="window.pwned=1">'
    TransferPage(browser, base).open().ready().fill(first_number(path, 2), "1", evil).submit().success()
    browser.get(base + "/dashboard")
    page = DashboardPage(browser, base).ready().open_statement()
    assert evil in page.descriptions()[0]
    assert browser.execute_script("return window.pwned === undefined")


def test_logout_ends_session(signed_in, browser, live_server):
    signed_in.logout()
    browser.get(live_server[0] + "/dashboard")
    assert browser.current_url == live_server[0] + "/"
