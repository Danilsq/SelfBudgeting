"""Thin psycopg2 layer. No ORM -- the schema is small and stable enough
that hand-written upserts are easier to reason about than mapping layers,
and it keeps the dependency list short for a locally-run personal tool."""
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras

from selfbudgeting.models import ParsedStatement, ParsedTransaction


def get_connection():
    dsn = os.environ.get("SELFBUDGETING_DSN", "postgresql://selfbudgeting:selfbudgeting@localhost:5432/selfbudgeting")
    return psycopg2.connect(dsn)


@contextmanager
def cursor() -> Iterator[psycopg2.extras.DictCursor]:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                yield cur
        conn.commit()
    finally:
        conn.close()


def upsert_account(cur, account) -> int:
    cur.execute(
        """
        INSERT INTO accounts (account_number, account_type, display_name, owner_name,
                               currency, card_mask, opened_date, closed_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_number) DO UPDATE SET
            display_name = COALESCE(EXCLUDED.display_name, accounts.display_name),
            owner_name   = COALESCE(EXCLUDED.owner_name, accounts.owner_name),
            card_mask    = COALESCE(EXCLUDED.card_mask, accounts.card_mask),
            closed_date  = COALESCE(EXCLUDED.closed_date, accounts.closed_date),
            updated_at   = now()
        RETURNING account_id
        """,
        (
            account.account_number,
            account.account_type,
            account.display_name,
            account.owner_name,
            account.currency,
            account.card_mask,
            account.opened_date,
            account.closed_date,
        ),
    )
    return cur.fetchone()["account_id"]


def insert_terms(cur, account_id: int, terms) -> None:
    if terms is None:
        return
    cur.execute(
        """
        INSERT INTO account_terms (account_id, effective_date, interest_rate, credit_limit,
                                    grace_period_days, term_type, term_length_days,
                                    term_end_date, contract_date, raw_meta)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, effective_date) DO UPDATE SET
            interest_rate = EXCLUDED.interest_rate,
            credit_limit = EXCLUDED.credit_limit,
            grace_period_days = EXCLUDED.grace_period_days,
            term_type = EXCLUDED.term_type,
            term_length_days = EXCLUDED.term_length_days,
            term_end_date = EXCLUDED.term_end_date,
            contract_date = EXCLUDED.contract_date,
            raw_meta = EXCLUDED.raw_meta
        """,
        (
            account_id,
            terms.effective_date,
            terms.interest_rate,
            terms.credit_limit,
            terms.grace_period_days,
            terms.term_type,
            terms.term_length_days,
            terms.term_end_date,
            terms.contract_date,
            psycopg2.extras.Json(terms.raw_meta or {}),
        ),
    )


def insert_statement(cur, account_id: int, statement: ParsedStatement, source_file: str, file_hash: str) -> int | None:
    cur.execute(
        """
        INSERT INTO statements (account_id, period_start, period_end, opening_balance,
                                 closing_balance, total_inflow, total_outflow, source_file,
                                 file_hash, parser_name, parser_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (file_hash) DO NOTHING
        RETURNING statement_id
        """,
        (
            account_id,
            statement.period_start,
            statement.period_end,
            statement.opening_balance,
            statement.closing_balance,
            statement.total_inflow,
            statement.total_outflow,
            source_file,
            file_hash,
            statement.parser_name,
            statement.parser_version,
        ),
    )
    row = cur.fetchone()
    return row["statement_id"] if row else None


def dedup_hash(account_number: str, txn: ParsedTransaction) -> str:
    """Deterministic identity for a transaction, independent of which
    statement export it was seen in -- this is what lets weekly re-exports
    (which always repeat some prior days) upsert cleanly instead of
    duplicating rows."""
    key = "|".join(
        [
            account_number,
            str(txn.operation_date),
            f"{txn.amount:.2f}",
            txn.description.strip().lower(),
            f"{txn.balance_after:.2f}" if txn.balance_after is not None else "",
            txn.doc_number or "",
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def insert_transactions(cur, account_id: int, statement_id: int, account_number: str, transactions) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for txn in transactions:
        h = dedup_hash(account_number, txn)
        cur.execute(
            """
            INSERT INTO transactions (account_id, statement_id, operation_date, processing_date,
                                       processing_time, auth_code, raw_category, description,
                                       amount, currency_amount, currency, balance_after,
                                       counter_account, doc_code, doc_number, direction, dedup_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (dedup_hash) DO NOTHING
            """,
            (
                account_id,
                statement_id,
                txn.operation_date,
                txn.processing_date,
                txn.processing_time,
                txn.auth_code,
                txn.raw_category,
                txn.description,
                txn.amount,
                txn.currency_amount,
                txn.currency,
                txn.balance_after,
                txn.counter_account,
                txn.doc_code,
                txn.doc_number,
                txn.direction,
                h,
            ),
        )
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1
    return inserted, skipped
