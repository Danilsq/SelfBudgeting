"""Shared helpers for turning Sberbank's Russian-formatted text into typed values."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

_NUM_RE = re.compile(r"[+-]?\d[\d\s ]*(?:[.,]\d+)?")


def parse_amount(raw: str) -> Optional[Decimal]:
    """'+15 536,91' / '-1 500,00' / '234 254,48' -> Decimal. Handles
    both regular and non-breaking spaces as thousands separators."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    m = _NUM_RE.search(raw)
    if not m:
        return None
    s = m.group(0)
    sign = "-" if s.strip().startswith("-") else ""
    s = s.replace(" ", "").replace(" ", "").lstrip("+-")
    s = s.replace(",", ".")
    try:
        value = Decimal(sign + s)
    except InvalidOperation:
        return None
    return value


def parse_date_ddmmyyyy(raw: str) -> Optional[date]:
    raw = raw.strip()
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", raw)
    if not m:
        return None
    d, mo, y = m.groups()
    return date(int(y), int(mo), int(d))


def parse_percent(raw: str) -> Optional[Decimal]:
    """'49.8% годовых' / '12.5% годовых' -> Decimal('49.8'). Returns None
    for placeholders like '2-' seen on deposits with tiered/variable rates
    (caller should fall back to storing the raw text in raw_meta)."""
    m = re.search(r"(\d+[.,]?\d*)\s*%", raw)
    if not m:
        return None
    return Decimal(m.group(1).replace(",", "."))


def normalize_account_number(raw: str) -> str:
    """'40817 810 6 0083 6676275' -> '40817810600836676275'."""
    return re.sub(r"[^\d]", "", raw)
