"""Weekly ingest CLI.

Usage:
    python -m selfbudgeting.ingest --input-dir ~/Downloads/sber-statements

Point it at a folder of PDFs exported from the Sberbank app (any mix of
debit/credit/savings/deposit statements, any filenames). For each file it:
    1. extracts text,
    2. sniffs the statement type + picks a matching parser version,
    3. parses accounts/terms/transactions,
    4. upserts everything into Postgres (safe to re-run on the same or
       overlapping files -- accounts are keyed by account number,
       statements by file hash, transactions by a content hash).

Run this manually once a week after exporting fresh statements. It prints
a per-file summary; nonzero exit code if any file failed to parse, so you
notice format drift immediately instead of silently losing a week.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from selfbudgeting import db
from selfbudgeting.extract import extract_text
from selfbudgeting.parsers import NoParserAvailable, detect_statement_type, find_parser
from selfbudgeting.parsers.sber_card_v1 import file_hash as compute_file_hash


def ingest_file(pdf_path: Path) -> str:
    text = extract_text(pdf_path)
    statement_type = detect_statement_type(text)
    parser = find_parser(statement_type, text)
    statement = parser.parse(text, source_file=pdf_path.name)
    fhash = compute_file_hash(pdf_path.read_bytes())

    with db.cursor() as cur:
        account_id = db.upsert_account(cur, statement.account)
        db.insert_terms(cur, account_id, statement.terms)
        statement_id = db.insert_statement(cur, account_id, statement, pdf_path.name, fhash)

        if statement_id is None:
            return f"  [skip] {pdf_path.name}: already imported (identical file)"

        inserted, skipped = db.insert_transactions(
            cur, account_id, statement_id, statement.account.account_number, statement.transactions
        )
        return (
            f"  [ok]   {pdf_path.name}: {statement_type} v{statement.parser_version} | "
            f"{statement.period_start}..{statement.period_end} | "
            f"{inserted} new txns, {skipped} already known"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest Sberbank statement PDFs into the selfbudgeting DB")
    ap.add_argument("--input-dir", required=True, type=Path, help="Folder containing exported PDF statements")
    args = ap.parse_args(argv)

    pdfs = sorted(args.input_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {args.input_dir}")
        return 1

    print(f"Found {len(pdfs)} PDF(s) in {args.input_dir}")
    failures = []
    for pdf_path in pdfs:
        try:
            print(ingest_file(pdf_path))
        except NoParserAvailable as e:
            failures.append(pdf_path.name)
            print(f"  [FAIL] {pdf_path.name}: {e}")
        except Exception as e:  # noqa: BLE001 -- surface any parse error per-file, keep going
            failures.append(pdf_path.name)
            print(f"  [FAIL] {pdf_path.name}: {type(e).__name__}: {e}")

    if failures:
        print(f"\n{len(failures)} file(s) failed: {', '.join(failures)}")
        print("These likely need a new parser version -- see selfbudgeting/parsers/base.py")
        return 1

    print("\nAll files ingested. Run `python -m selfbudgeting.categorize` next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
