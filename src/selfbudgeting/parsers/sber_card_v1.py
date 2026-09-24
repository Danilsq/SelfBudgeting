"""Parser v1 for Sberbank debit- and credit-card statement PDFs
('Выписка по счёту дебетовой/кредитной карты').

Fitted against the credit-card sample text in this project (period
14.10.2025-21.08.2026). The debit-card layout is assumed to be the same
minus the credit-only fields (credit limit / interest rate / grace period /
total debt) -- confirm this against a real debit-card export and adjust
`sniff()` / field extraction once you've run it once. That's exactly the
kind of drift this versioned-parser setup exists to catch: if a real debit
statement doesn't match, `sniff()` will (correctly) refuse it and
`find_parser` will raise `NoParserAvailable` instead of silently
mis-parsing, telling you it's time for a v2.

Row shape inside "Расшифровка операций", one record per transaction:

    <op_date>\\n<processing_date>\\n<HH:MM>\\n<auth_code>\\n
    <category>\\n<description ... possibly multi-line>\\n
    <sign?><amount>,<kop> <balance>,<kop>

A leading '+' on the amount means money in (direction=credit); no sign
means money out (direction=debit, stored as a negative signed amount).
Lines that don't start with two dates+time+code (e.g. the
"Погашение процентов 11 467,37" annotation line Sberbank sometimes drops
between records) are simply skipped by the regex scan.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal

from selfbudgeting.models import ParsedAccount, ParsedStatement, ParsedTerms, ParsedTransaction
from selfbudgeting.parsers.base import StatementParser, register
from selfbudgeting.parsers.utils import (
    normalize_account_number,
    parse_amount,
    parse_date_ddmmyyyy,
    parse_percent,
)

_RECORD_RE = re.compile(
    r"(?P<op_date>\d{2}\.\d{2}\.\d{4})\s*\n"
    r"(?P<proc_date>\d{2}\.\d{2}\.\d{4})\s*\n"
    r"(?P<proc_time>\d{2}:\d{2})\s*\n"
    r"(?P<auth_code>\d{6})\s*\n"
    r"(?P<category>[^\n]+)\n"
    r"(?P<description>.*?)\n"
    r"(?P<amount>[+-]?[\d\s ]+,\d{2})\s+(?P<balance>[\d\s ]+,\d{2})",
    re.DOTALL,
)


class _CardStatementParserV1(StatementParser):
    version = "v1"

    def parse(self, text: str, source_file: str) -> ParsedStatement:
        owner = _search(text, r"Владелец счёта\s*\n([^\n]+)")
        account_number = normalize_account_number(
            _search(text, r"Номер счёта\s+([\d\s]+\d)") or ""
        )
        display_name = _search(text, r"Карта\s+([^\n]+)")
        mask = _search(text, r"••\s*(\d{3,4})")
        currency = _search(text, r"Валюта\s+([^\n]+)") or "Российский рубль"

        period_start, period_end = _parse_period(text)

        opening_balance = _first_balance_after(text, r"Остаток на\s+" + re.escape(_fmt(period_start)))
        closing_balance = _first_balance_after(text, r"Остаток на\s+" + re.escape(_fmt(period_end)))
        total_inflow = parse_amount(_search(text, r"Пополнение\s+([\d\s ,]+)") or "")
        total_outflow = parse_amount(
            _search(text, r"Списание\s*(?:с учётом[^\n]*\n)?\s*([\d\s ,]+)") or ""
        )

        opened_raw = _search(text, r"Дата открытия счёта\s+([^\n]+)")
        closed_raw = _search(text, r"Дата закрытия счёта\s+([^\n]+)")
        opened_date = parse_date_ddmmyyyy(opened_raw) if opened_raw and opened_raw.strip() != "-" else None
        closed_date = parse_date_ddmmyyyy(closed_raw) if closed_raw and closed_raw.strip() != "-" else None

        account = ParsedAccount(
            account_number=account_number,
            account_type=self.statement_type,
            owner_name=owner,
            display_name=display_name,
            currency="RUB" if currency and "рубл" in currency.lower() else (currency or "RUB"),
            card_mask=mask,
            opened_date=opened_date,
            closed_date=closed_date,
        )

        transactions = []
        for m in _RECORD_RE.finditer(text):
            raw_amount = m.group("amount").strip()
            signed = parse_amount(raw_amount) or Decimal("0")
            direction = "credit" if raw_amount.startswith("+") else "debit"
            if direction == "debit" and signed > 0:
                signed = -signed
            description = re.sub(r"\s*\n\s*", " ", m.group("description")).strip()
            transactions.append(
                ParsedTransaction(
                    operation_date=parse_date_ddmmyyyy(m.group("op_date")),
                    processing_date=parse_date_ddmmyyyy(m.group("proc_date")),
                    processing_time=datetime.strptime(m.group("proc_time"), "%H:%M").time(),
                    auth_code=m.group("auth_code"),
                    raw_category=m.group("category").strip(),
                    description=description,
                    amount=signed,
                    direction=direction,
                    balance_after=parse_amount(m.group("balance")),
                    currency=account.currency,
                )
            )

        return ParsedStatement(
            account=account,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_inflow=total_inflow,
            total_outflow=total_outflow,
            transactions=transactions,
            terms=self._extract_terms(text, period_end),
            parser_name=self.__class__.__name__,
            parser_version=self.version,
        )

    def _extract_terms(self, text: str, effective_date: date) -> ParsedTerms | None:
        return None


@register
class SberCreditCardV1(_CardStatementParserV1):
    statement_type = "credit_card"

    @classmethod
    def sniff(cls, text: str) -> bool:
        head = text[:900]
        return "кредитной карты" in head and "Кредитный лимит" in text[:2000]

    def _extract_terms(self, text: str, effective_date: date) -> ParsedTerms | None:
        limit = parse_amount(_search(text, r"Кредитный лимит\s+([\d\s ,]+)") or "")
        rate = parse_percent(_search(text, r"Процентная ставка\s+([\d.,]+%[^\n]*)") or "")
        grace = _search(text, r"Льготный период\s+До\s+(\d+)\s*дней")
        return ParsedTerms(
            effective_date=effective_date,
            interest_rate=rate,
            credit_limit=limit,
            grace_period_days=int(grace) if grace else None,
            term_type="credit_card",
        )


@register
class SberDebitCardV1(_CardStatementParserV1):
    statement_type = "debit_card"

    @classmethod
    def sniff(cls, text: str) -> bool:
        head = text[:900]
        return ("дебетовой карты" in head) and "Кредитный лимит" not in text[:2000]


def _search(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


def _parse_period(text: str) -> tuple[date, date]:
    m = re.search(r"За период\s+(\d{2}\.\d{2}\.\d{4})\s*[—-]\s*(\d{2}\.\d{2}\.\d{4})", text)
    if not m:
        raise ValueError("Could not find 'За период <start> — <end>' line")
    return parse_date_ddmmyyyy(m.group(1)), parse_date_ddmmyyyy(m.group(2))


def _fmt(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _first_balance_after(text: str, label_pattern: str) -> Decimal | None:
    m = re.search(label_pattern + r"\s+([\d\s ,]+)", text)
    return parse_amount(m.group(1)) if m else None


def file_hash(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()
