"""Parser interface + a tiny version registry.

Why versioned parsers at all: the bank changes its PDF export layout
without warning, and old statements you already imported were parsed by
whatever code existed at the time. Rather than one `parse_credit_card()`
function that you keep editing in place (silently breaking re-imports of
old PDFs), each layout gets its own class with a fixed `version` string.

How a PDF is matched to a parser:
    1. `sniff()` on each registered parser for the file's statement_type
       is tried, most-recently-added first.
    2. The first one that returns True on the extracted text parses it.
    3. If none match, `NoParserAvailable` is raised so you notice the bank
       changed the format instead of silently mis-parsing.

When the bank changes the layout: copy the newest parser file for that
statement type to a new version (e.g. sber_credit_card_v1.py ->
sber_credit_card_v2.py), bump `version`, tighten `sniff()` so old and new
files each match exactly one parser, and register the new class below the
old one (registration order = sniff order, newest first).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from selfbudgeting.models import ParsedStatement


class NoParserAvailable(Exception):
    pass


class StatementParser(ABC):
    statement_type: ClassVar[str]   # 'debit_card' | 'credit_card' | 'savings' | 'deposit'
    version: ClassVar[str]          # e.g. 'v1'

    @classmethod
    @abstractmethod
    def sniff(cls, text: str) -> bool:
        """Return True if this parser's version of the layout produced `text`."""

    @abstractmethod
    def parse(self, text: str, source_file: str) -> ParsedStatement:
        """Parse the full extracted PDF text into a ParsedStatement."""


_REGISTRY: dict[str, list[type[StatementParser]]] = {}


def register(parser_cls: type[StatementParser]) -> type[StatementParser]:
    """Class decorator: adds a parser to the registry for its statement_type."""
    _REGISTRY.setdefault(parser_cls.statement_type, []).insert(0, parser_cls)
    return parser_cls


def all_parsers() -> dict[str, list[type[StatementParser]]]:
    return _REGISTRY


def find_parser(statement_type: str, text: str) -> StatementParser:
    for parser_cls in _REGISTRY.get(statement_type, []):
        if parser_cls.sniff(text):
            return parser_cls()
    raise NoParserAvailable(
        f"No registered parser for statement_type={statement_type!r} matched this file. "
        f"The bank likely changed its PDF layout -- add a new parser version "
        f"(see selfbudgeting/parsers/base.py docstring)."
    )


def detect_statement_type(text: str) -> str:
    """Rough sniff of which of the 4 statement kinds a PDF's text belongs to,
    based on the title line Sberbank prints on every statement."""
    head = text[:800]
    if "кредитной карты" in head:
        return "credit_card"
    if "дебетовой карты" in head or "дебетовой" in head:
        return "debit_card"
    if "вкладу" in head or "Вклад" in head:
        return "deposit"
    if "Накопительный счёт" in head or "счёту «Накопительный" in head or "накопительн" in head.lower():
        return "savings"
    raise NoParserAvailable(
        "Could not determine statement type (debit/credit/savings/deposit) from the "
        "document header. First ~400 chars were:\n" + head
    )
