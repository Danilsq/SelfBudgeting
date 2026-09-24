"""Parser v1 for Sberbank savings-account and term-deposit statement PDFs
('Выписка по счёту «Накопительный счёт»' / 'Выписка по вкладу «...»').

Both share the same transaction-table layout:

    <op_date> <op_name>[\\n]
    [к/с <counter_account>\\n]              -- present when money moved to/from
                                              another account; absent for pure
                                              interest events like "Закрытие счета"
    <doc_code>, № <doc_number> <sign><amount>,<kop> <balance>,<kop>

`op_name` values seen so far: Зачисление, Списание, Начисление процентов,
Капитализация вклада, Пролонгация, Закрытие счета. These aren't a bank
"category" in the card sense -- map them to categories via
category_rules on raw_category (op_name is stored there) rather than
trying to guess spend categories, since savings/deposit activity is
mostly your own transfers and interest, not purchases.

Deposits (`СберВклад` etc.) add contract/term fields that savings
accounts don't have (Срок счёта, Дата заключения/закрытия договора,
prolongation date) -- see `_extract_terms`.
"""
from __future__ import annotations

import re
from datetime import date

from selfbudgeting.models import ParsedAccount, ParsedStatement, ParsedTerms, ParsedTransaction
from selfbudgeting.parsers.base import StatementParser, register
from selfbudgeting.parsers.utils import (
    normalize_account_number,
    parse_amount,
    parse_date_ddmmyyyy,
    parse_percent,
)

_RECORD_RE = re.compile(
    r"(?P<op_date>\d{2}\.\d{2}\.\d{4})\s+(?P<op_name>[^\n\d]+?)\s*\n?"
    r"(?:\s*к/с\s+(?P<counter>[\d\s]+\d)\s*\n)?"
    r"\s*(?P<doc_code>-?\d{1,2}),\s*№\s*(?P<doc_number>[\w-]+)\s+"
    r"(?P<amount>[+-][\d\s ]+,\d{2})\s+(?P<balance>[\d\s ]+,\d{2})"
)


class _SavingsDepositParserV1(StatementParser):
    version = "v1"

    def parse(self, text: str, source_file: str) -> ParsedStatement:
        owner = _search(text, r"Владелец (?:счёта|вклада)\s*\n([^\n]+)")
        account_number = normalize_account_number(
            _search(text, r"Номер счёта\s+([\d\s]+\d)") or ""
        )
        acct_type_label = _search(text, r"Тип счёта\s+([^\n]+)")
        currency = _search(text, r"Валюта\s+([^\n]+)") or "Российский рубль"

        period_start, period_end = _parse_period(text)
        opening_balance = _first_balance_after(text, r"Остаток на\s+" + re.escape(_fmt(period_start)))
        closing_balance = _first_balance_after(text, r"Остаток на\s+" + re.escape(_fmt(period_end)))
        total_inflow = parse_amount(_search(text, r"Пополнение\s*\n(?:[^\n]*\n)*?\s*([\d\s ,]+)\n") or "")
        total_outflow = parse_amount(_search(text, r"Списание\s+([\d\s ,]+)") or "")

        account = ParsedAccount(
            account_number=account_number,
            account_type=self.statement_type,
            owner_name=owner,
            display_name=acct_type_label,
            currency="RUB" if currency and "рубл" in currency.lower() else (currency or "RUB"),
        )

        transactions = []
        for m in _RECORD_RE.finditer(text):
            raw_amount = m.group("amount").strip()
            signed = parse_amount(raw_amount)
            direction = "credit" if raw_amount.startswith("+") else "debit"
            transactions.append(
                ParsedTransaction(
                    operation_date=parse_date_ddmmyyyy(m.group("op_date")),
                    raw_category=m.group("op_name").strip(),
                    description=m.group("op_name").strip(),
                    amount=signed,
                    direction=direction,
                    balance_after=parse_amount(m.group("balance")),
                    counter_account=(
                        normalize_account_number(m.group("counter")) if m.group("counter") else None
                    ),
                    doc_code=m.group("doc_code"),
                    doc_number=m.group("doc_number"),
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
            terms=self._extract_terms(text, period_end, acct_type_label),
            parser_name=self.__class__.__name__,
            parser_version=self.version,
        )

    def _extract_terms(self, text: str, effective_date: date, term_type: str | None) -> ParsedTerms | None:
        return ParsedTerms(effective_date=effective_date, term_type=term_type)


@register
class SberSavingsV1(_SavingsDepositParserV1):
    statement_type = "savings"

    @classmethod
    def sniff(cls, text: str) -> bool:
        head = text[:900]
        return "Накопительный счёт" in head or "счёту «Накопительный" in head

    def _extract_terms(self, text: str, effective_date: date, term_type: str | None) -> ParsedTerms | None:
        rate = parse_percent(_search(text, r"Процентная ставка\s+([^\n]+)") or "")
        return ParsedTerms(effective_date=effective_date, interest_rate=rate, term_type=term_type)


@register
class SberDepositV1(_SavingsDepositParserV1):
    statement_type = "deposit"

    @classmethod
    def sniff(cls, text: str) -> bool:
        head = text[:900]
        return "вкладу" in head and "Накопительный" not in head

    def _extract_terms(self, text: str, effective_date: date, term_type: str | None) -> ParsedTerms | None:
        rate_raw = _search(text, r"Процентная ставка\s+([^\n]+)") or ""
        rate = parse_percent(rate_raw)
        term_len_raw = _search(text, r"Срок счёта\s+([^\n]+)") or ""
        term_len_days = int(re.match(r"(\d+)", term_len_raw).group(1)) if re.match(r"(\d+)", term_len_raw) else None
        term_end_raw = _search(
            text, r"Дата окончания первоначального\s*/пролонгированного срока\s*\n\s*(\d{2}\.\d{2}\.\d{4})"
        )
        contract_raw = _search(text, r"Дата заключения договора\s+([^\n]+)")
        closed_raw = _search(text, r"Дата закрытия договора\s+([^\n]+)")
        return ParsedTerms(
            effective_date=effective_date,
            interest_rate=rate,
            term_type=term_type,
            term_length_days=term_len_days,
            term_end_date=parse_date_ddmmyyyy(term_end_raw) if term_end_raw else None,
            contract_date=parse_date_ddmmyyyy(contract_raw) if contract_raw else None,
            raw_meta={
                "rate_raw": rate_raw.strip(),
                "closed_raw": closed_raw.strip() if closed_raw else None,
            },
        )


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


def _first_balance_after(text: str, label_pattern: str) -> "None | object":
    m = re.search(label_pattern + r"\s+([\d\s ,]+)", text)
    return parse_amount(m.group(1)) if m else None
