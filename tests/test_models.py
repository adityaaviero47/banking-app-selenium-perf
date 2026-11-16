import pytest

from app.models import InvalidTransfer, parse_amount


@pytest.mark.parametrize("text,paise", [
    ("1500", 150000), ("1500.5", 150050), ("1500.50", 150050), ("0.01", 1),
    (" 25 ", 2500), ("1000000", 100000000),
])
def test_valid_amounts(text, paise):
    assert parse_amount(text) == paise


@pytest.mark.parametrize("text", [
    "", "0", "0.00", "-5", "abc", "1.234", "1..2", "1e3", "1,000", "1000000.01", ".",
])
def test_invalid_amounts(text):
    with pytest.raises(InvalidTransfer):
        parse_amount(text)
