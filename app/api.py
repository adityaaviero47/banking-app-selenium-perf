"""FastAPI application: JSON API plus the static front end.

Set BANK_DB (database file) and BANK_MODE (tuned | baseline) to choose the
data-access implementation.
"""
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .models import BankError
from .repositories import (AccountRepository, BaselineStatementRepository, Database,
                           TunedStatementRepository)
from .services import AuthService, TransferService

STATIC = Path(__file__).parent / "static"


class LoginIn(BaseModel):
    customer_id: int
    password: str


class TransferIn(BaseModel):
    from_account_id: int
    to_account_number: str = Field(max_length=20)
    amount: str = Field(max_length=20)
    description: str = Field(default="", max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=64)


def create_app(db_file=None, mode=None):
    db_file = db_file or os.environ.get("BANK_DB", "bank.db")
    mode = mode or os.environ.get("BANK_MODE", "tuned")
    database = Database(db_file, tuned=(mode == "tuned"))
    accounts = AccountRepository(database)
    statements = (TunedStatementRepository if mode == "tuned"
                  else BaselineStatementRepository)(database)
    auth = AuthService(database)
    transfers = TransferService(database, accounts)

    app = FastAPI(title="Retail Banking Demo", version="1.0")
    app.state.auth = auth
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(BankError)
    def bank_error(_, exc: BankError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    def token_of(request: Request):
        h = request.headers.get("authorization", "")
        return h[7:] if h.lower().startswith("bearer ") else request.cookies.get("session")

    def current_customer(request: Request):
        return auth.customer_for(token_of(request))

    # ---- API -------------------------------------------------------------
    @app.post("/api/login")
    def login(body: LoginIn):
        token, name = auth.login(body.customer_id, body.password)
        resp = JSONResponse({"token": token, "name": name})
        resp.set_cookie("session", token, httponly=True, samesite="strict")
        return resp

    @app.post("/api/logout")
    def logout(request: Request):
        auth.logout(token_of(request))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("session")
        return resp

    @app.get("/api/accounts")
    def list_accounts(cid: int = Depends(current_customer)):
        return [a.to_dict() for a in accounts.for_customer(cid)]

    @app.get("/api/accounts/{account_id}/statement")
    def statement(account_id: int, limit: int = 25, cursor: str | None = None,
                  q: str | None = None, cid: int = Depends(current_customer)):
        acct = accounts.get(account_id)
        if acct.customer_id != cid:
            raise_not_found()
        limit = max(1, min(limit, 100))
        rows, nxt = statements.page(account_id, limit, cursor, (q or "").strip() or None)
        return {"account": acct.to_dict(), "transactions": [t.to_dict() for t in rows],
                "next_cursor": nxt}

    @app.post("/api/transfers")
    def transfer(body: TransferIn, cid: int = Depends(current_customer)):
        return transfers.transfer(cid, body.from_account_id, body.to_account_number,
                                  body.amount, body.description, body.idempotency_key)

    # ---- pages -----------------------------------------------------------
    def page(name):
        return FileResponse(STATIC / name)

    @app.get("/")
    def index():
        return page("login.html")

    @app.get("/dashboard")
    def dashboard(request: Request):
        return page("dashboard.html") if _logged_in(request) else RedirectResponse("/")

    @app.get("/statement")
    def statement_page(request: Request):
        return page("statement.html") if _logged_in(request) else RedirectResponse("/")

    @app.get("/transfer")
    def transfer_page(request: Request):
        return page("transfer.html") if _logged_in(request) else RedirectResponse("/")

    def _logged_in(request):
        try:
            current_customer(request)
            return True
        except BankError:
            return False

    return app


def raise_not_found():
    from .models import NotFound
    raise NotFound("Account not found")


app = create_app() if os.environ.get("BANK_DB") else None
