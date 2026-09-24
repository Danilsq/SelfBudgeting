"""Importing this package registers every parser version. Add new parser
modules here as you create them (see base.py docstring for when/how)."""
from selfbudgeting.parsers import base  # noqa: F401
from selfbudgeting.parsers import sber_card_v1  # noqa: F401
from selfbudgeting.parsers import sber_savings_deposit_v1  # noqa: F401

from selfbudgeting.parsers.base import (  # noqa: F401
    NoParserAvailable,
    StatementParser,
    all_parsers,
    detect_statement_type,
    find_parser,
)
