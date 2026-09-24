"""These fixtures are copied verbatim from real (personal) statement
excerpts, so a passing test here is real evidence the regexes work on the
bank's actual text -- not just on data we made up to fit the code."""
from decimal import Decimal
from pathlib import Path

from selfbudgeting.parsers import detect_statement_type, find_parser

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_detect_and_parse_credit_card():
    text = _load("credit_card_sample.txt")
    assert detect_statement_type(text) == "credit_card"
    parser = find_parser("credit_card", text)
    stmt = parser.parse(text, source_file="credit_card_sample.txt")

    assert stmt.account.account_number == "40817810600836676275"
    assert stmt.account.card_mask == "1824"
    assert stmt.terms.credit_limit == Decimal("210000.00")
    assert stmt.terms.interest_rate == Decimal("49.8")
    assert stmt.terms.grace_period_days == 120

    assert len(stmt.transactions) == 4
    t0 = stmt.transactions[0]
    assert t0.direction == "credit"
    assert t0.amount == Decimal("15536.91")
    assert t0.raw_category == "Перевод на карту"
    assert "Операция по карте" in t0.description

    t1 = stmt.transactions[1]
    assert t1.direction == "debit"
    assert t1.amount == Decimal("-95.00")
    assert t1.raw_category == "Транспорт"


def test_detect_and_parse_savings():
    text = _load("savings_sample.txt")
    assert detect_statement_type(text) == "savings"
    parser = find_parser("savings", text)
    stmt = parser.parse(text, source_file="savings_sample.txt")

    assert stmt.account.account_number == "40817810955191194485"
    assert len(stmt.transactions) == 3

    closure = stmt.transactions[0]
    assert closure.direction == "debit"
    assert closure.amount == Decimal("-10000.00")
    assert closure.counter_account is None

    deposit_in = stmt.transactions[1]
    assert deposit_in.direction == "credit"
    assert deposit_in.amount == Decimal("50000.00")
    assert deposit_in.counter_account == "40817810955191194485"


def test_detect_and_parse_deposit():
    text = _load("deposit_sample.txt")
    assert detect_statement_type(text) == "deposit"
    parser = find_parser("deposit", text)
    stmt = parser.parse(text, source_file="deposit_sample.txt")

    assert stmt.account.account_number == "42305810855172078282"
    assert stmt.terms.term_length_days == 184
    assert str(stmt.terms.term_end_date) == "2026-01-24"

    assert len(stmt.transactions) == 3
    assert stmt.transactions[0].raw_category == "Закрытие счета"
    assert stmt.transactions[0].amount == Decimal("-415329.76")
    assert stmt.transactions[2].raw_category == "Пролонгация"
