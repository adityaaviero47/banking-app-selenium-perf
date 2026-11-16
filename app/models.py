"""Domain objects and errors."""
from dataclasses import dataclass


class BankError(Exception):
    """Base class for errors shown to the user."""
    status = 400


class AuthError(BankError):
    status = 401


class NotFound(BankError):
    status = 404


class InsufficientFunds(BankError):
    status = 422


class InvalidTransfer(BankError):
    status = 422


@dataclass(frozen=True)
class Account:
    id: int
    customer_id: int
    number: str
    kind: str
    balance: int            # paise

    def to_dict(self):
        return {"id": self.id, "number": self.number, "kind": self.kind,
                "balance": self.balance}


@dataclass(frozen=True)
class Transaction:
    id: int
    amount: int
    balance_after: int
    description: str
    ts: str
    counterparty: str | None

    def to_dict(self):
        return {"id": self.id, "amount": self.amount, "balance_after": self.balance_after,
                "description": self.description, "ts": self.ts,
                "counterparty": self.counterparty}


def parse_amount(text):
    """Parse a rupee amount typed by a user into paise. Rejects bad input."""
    s = str(text).strip()
    if not s or s.count(".") > 1 or not s.replace(".", "", 1).isdigit():
        raise InvalidTransfer("Enter an amount like 1500 or 1500.50")
    whole, _, frac = s.partition(".")
    if len(frac) > 2:
        raise InvalidTransfer("Amounts can have at most 2 decimal places")
    paise = int(whole or "0") * 100 + int((frac + "00")[:2])
    if paise <= 0:
        raise InvalidTransfer("Amount must be more than zero")
    if paise > 10_00_000_00:                       # INR 10 lakh per transfer
        raise InvalidTransfer("Amount is above the INR 10,00,000 per-transfer limit")
    return paise
