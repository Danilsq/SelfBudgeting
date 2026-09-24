"""Stage-1 categorization: rule-based only. No local LLM yet -- this file
is deliberately kept as a separate, swappable step so that a later local
LLM pass (Ollama, etc.) can slot in as `category_source = 'llm'` on top of
whatever the rules already decided, without changing the DB schema or
the ingest pipeline. See README.md "Roadmap" for that stage.

How it works: every active row in category_rules is a regex tested
against either the transaction's description or the bank's raw_category,
in priority order (lowest number first). First match wins. Anything left
uncategorized keeps category_id = NULL, category_source = NULL, and
should show up in your dashboard as "needs review" so you can add a rule
or (later) let the LLM take a pass at it.
"""
from __future__ import annotations

import re

from selfbudgeting.db import cursor


def apply_rules(account_id: int | None = None) -> tuple[int, int]:
    """Categorize any transaction that doesn't have a category yet.
    Returns (matched_count, still_uncategorized_count)."""
    with cursor() as cur:
        cur.execute(
            "SELECT rule_id, pattern, match_field, category_id, priority "
            "FROM category_rules WHERE active ORDER BY priority ASC"
        )
        rules = [
            (r["rule_id"], re.compile(r["pattern"], re.IGNORECASE), r["match_field"], r["category_id"])
            for r in cur.fetchall()
        ]

        where = "category_id IS NULL"
        params: list = []
        if account_id is not None:
            where += " AND account_id = %s"
            params.append(account_id)

        cur.execute(
            f"SELECT transaction_id, description, raw_category FROM transactions WHERE {where}",
            params,
        )
        rows = cur.fetchall()

        matched = 0
        for row in rows:
            for _rule_id, pattern, match_field, category_id in rules:
                haystack = row[match_field] or ""
                if pattern.search(haystack):
                    cur.execute(
                        "UPDATE transactions SET category_id = %s, category_source = 'rule' "
                        "WHERE transaction_id = %s",
                        (category_id, row["transaction_id"]),
                    )
                    matched += 1
                    break

        still_uncategorized = len(rows) - matched
        return matched, still_uncategorized


if __name__ == "__main__":
    matched, remaining = apply_rules()
    print(f"Categorized {matched} transactions via rules; {remaining} still uncategorized.")
