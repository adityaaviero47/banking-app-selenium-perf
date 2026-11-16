import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import create_app  # noqa: E402
from app.seed import PASSWORD, seed  # noqa: E402


@pytest.fixture(scope="session")
def bank_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "test_bank.db"
    info = seed(path, customers=40, transactions=20_000, business_accounts=5)
    from app import db
    conn = db.connect(path)
    db.apply_tuning(conn)
    conn.close()
    return path, info


@pytest.fixture(params=["tuned", "baseline"])
def client(request, bank_db, tmp_path):
    """API client on a fresh copy of the bank, for both data-access modes."""
    from fastapi.testclient import TestClient
    import shutil
    path = tmp_path / "bank.db"
    shutil.copy(bank_db[0], path)
    return TestClient(create_app(str(path), request.param))


def login(client, customer_id=1, password=PASSWORD):
    r = client.post("/api/login", json={"customer_id": customer_id, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(bank_db, tmp_path_factory):
    """Real uvicorn server in a background thread, for the Selenium suite."""
    import shutil
    path = tmp_path_factory.mktemp("live") / "bank.db"
    shutil.copy(bank_db[0], path)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(str(path), "tuned"), host="127.0.0.1",
                                           port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", path
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture(scope="session")
def browser():
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    opts = webdriver.ChromeOptions()
    for a in ("--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1280,900"):
        opts.add_argument(a)
    try:
        driver = webdriver.Chrome(options=opts)          # Selenium Manager finds a driver
    except Exception:
        # offline machines: a chromedriver matching the local Chrome, from PyPI
        from chromedriver_py import binary_path
        driver = webdriver.Chrome(service=Service(binary_path), options=opts)
    driver.implicitly_wait(0)
    yield driver
    driver.quit()
