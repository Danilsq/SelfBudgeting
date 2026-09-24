"""Plain dataclasses that a parser produces. Nothing here talks to the DB --
that keeps parsers testable without a running Postgres instance."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from typing import Any, Literal, Optional

AccountType = Literal["debit_card", "credit_card", "savings", "deposit"]
Direction = Literal["debit", "credit"]


@dataclass
class ParsedAccount:
    account_number: str
    account_type: AccountType
    owner_name: Optional[str] = None
    display_name: Optional[str] = None
    currency: str = "RUB"
    card_mask: Optional[str] = None
    opened_date: Optional[date] = None
    closed_date: Optional[date] = None


@dataclass
class ParsedTerms:
    effective_date: date
    interest_rate: Optional[Decimal] = None
    credit_limit: Optional[Decimal] = None
    grace_period_days: Optional[int] = None
    term_type: Optional[str] = None
    term_length_days: Optional[int] = None
    term_end_date: Optional[date] = None
    contract_date: Optional[date] = None
    raw_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedTransaction:
    operation_date: date
    description: str
    amount: Decimal                 # signed: negative = money out, positive = money in
    direction: Direction
    processing_date: Optional[date] = None
    processing_time: Optional[time] = None
    auth_code: Optional[str] = None
    raw_category: Optional[str] = None
    currency_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    balance_after: Optional[Decimal] = None
    counter_account: Optional[str] = None
    doc_code: Optional[str] = None
    doc_number: Optional[str] = None


@dataclass
class ParsedStatement:
    account: ParsedAccount
    period_start: date
    period_end: date
    opening_balance: Optional[Decimal]
    closing_balance: Optional[Decimal]
    total_inflow: Optional[Decimal]
    total_outflow: Optional[Decimal]
    transactions: list[ParsedTransaction]
    terms: Optional[ParsedTerms] = None
    parser_name: str = ""
    parser_version: str = ""
