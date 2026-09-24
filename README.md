# selfbudgeting

Personal finance infrastructure: parse weekly Sberbank PDF exports (debit
card, credit card, savings account, term deposit) into a local Postgres
database, categorize transactions, and (in later stages) analyze, dashboard,
and forecast from it. This is **stage 1**: parsing + storage foundation.

Everything runs on your own machine. Nothing is sent anywhere.

## Setup

Requires Docker (for Postgres) and Python 3.10+.

```bash
cd selfbudgeting
cp .env.example .env          # only if you change docker-compose's DB credentials
docker compose up -d          # starts Postgres, auto-runs db/schema.sql + seed_categories.sql
pip install -e .
pip install pytest            # optional, for running tests
```

Verify the DB came up and the schema loaded:

```bash
docker exec -it selfbudgeting_db psql -U selfbudgeting -d selfbudgeting -c '\dt'
```

## Weekly workflow

1. Export fresh statements from the Sberbank app for each of your accounts
   (debit card, credit card, savings, deposit) as PDF, into one folder.
   Overlap with last week's export is fine and expected — ingestion dedupes.
2. Run:

   ```bash
   python -m selfbudgeting.ingest --input-dir /path/to/that/folder
   ```

   This parses every PDF, upserts accounts/terms/statements/transactions,
   and prints a per-file summary. If a file fails with `NoParserAvailable`
   or a parse error, the bank likely changed its PDF layout — see
   "When a parser breaks" below. Everything else still imports; only the
   failing file(s) are skipped.

3. Run the rule-based categorizer:

   ```bash
   python -m selfbudgeting.categorize
   ```

   This uses `category_rules` in the DB (seeded empty — see below) to tag
   transactions. Anything it can't match stays `category_id = NULL` /
   "needs review".

## Why it's built this way

**Database: Postgres**, run locally via `docker compose` (`docker-compose.yml`).
Chosen over SQLite because you'll eventually have several local processes
(ingest, categorization agent, forecasting model, dashboard) reading and
writing concurrently — Postgres handles that cleanly, SQLite gets awkward
with concurrent writers.

**Schema** (`db/schema.sql`) — the core tension is that debit/credit
cards, savings accounts, and deposits look different (a card has category +
auth code; a deposit has a term and prolongation events; a savings account
has neither) but you want to analyze them together (net worth, cash flow
across everything). Resolved as:

- `accounts` — one row per account, whatever its type (`account_type`
  distinguishes debit_card / credit_card / savings / deposit).
- `account_terms` — a **time series** of interest rate / credit limit /
  term info per account, one snapshot per statement period, instead of
  overwriting current terms in place. Rates and limits change; you'll
  want to see that history later, e.g. for forecasting.
- `statements` — one row per PDF you ever import (period, opening/closing
  balance, which parser version produced it). This is your audit trail.
- `transactions` — one unified table for every account type. Card-only
  fields (auth_code, raw_category) and savings/deposit-only fields
  (counter_account, doc_code) just sit NULL on the rows that don't apply.
  A `direction` + signed `amount` convention (negative = money out) makes
  "sum everything" queries trivial regardless of account type.
- `categories` / `category_rules` — normalized categories you control,
  separate from whatever label the bank's raw statement used
  (`raw_category`), with a simple regex-rule engine to auto-assign them.
  `category_source` (`bank` / `rule` / `llm` / `manual`) tracks how a
  transaction got its category, since a later stage adds an LLM pass.

**Dedup, not "only new rows"**: Sberbank's exports always overlap with the
prior export (last week's rows reappear in this week's PDF). Rather than
trying to detect "new since last time" positionally, every transaction gets
a deterministic hash (account + date + amount + description + balance) and
we `INSERT ... ON CONFLICT DO NOTHING`. Re-running ingest on the same or
overlapping files is always safe.

**Versioned parsers** (`src/selfbudgeting/parsers/`): the bank will change
its PDF layout eventually (you mentioned this happens already). Each
statement-type parser is a class with a `version` and a `sniff()` method
that recognizes its own layout. `find_parser()` tries registered parsers
newest-first and picks the one whose `sniff()` matches. If none match, you
get a loud `NoParserAvailable` instead of a silently wrong parse — that's
your signal to add a new version. See the docstring in `parsers/base.py`
for the exact steps.

## When a parser breaks

1. `python -m selfbudgeting.ingest` will tell you which file(s) failed.
2. Open the PDF, extract its text (`selfbudgeting.extract.extract_text`),
   and compare it to the parser's docstring / regexes in
   `parsers/sber_card_v1.py` or `parsers/sber_savings_deposit_v1.py`.
3. Copy the relevant parser class to a new version (bump `version`,
   e.g. `v1` -> `v2`), fix its regexes, tighten both versions' `sniff()`
   so old and new statements each match exactly one, and register the new
   class in `parsers/__init__.py`.
4. Add the new file's text as a fixture under `tests/fixtures/` and a test
   in `tests/test_parsers.py` (this is how the current parsers were
   validated — against real statement text, not guesses).

## Current state / known gaps (stage 1)

- The debit-card parser (`SberDebitCardV1`) is written by mirroring the
  credit-card layout minus credit-only fields — **not yet validated
  against a real debit-card statement's extracted text**. Run ingest on
  one; if `sniff()` rejects it, that's expected (it means the layout
  differs) — copy the credit-card fixture pattern in `tests/` to fix it.
- `category_rules` starts **empty**. Add rows for your own spending
  patterns, e.g.:

  ```sql
  INSERT INTO category_rules (pattern, match_field, category_id, priority)
  SELECT 'YANDEX\*4121\*GO', 'description', category_id, 100
  FROM categories WHERE name = 'Такси';
  ```

  (`raw_category` from the bank, e.g. "Транспорт", "Рестораны и кафе", is
  also available as a match_field and is often a good enough first rule:
  `pattern='^Транспорт$', match_field='raw_category'`.)

## Roadmap (not built yet)

- **Local LLM categorization agent** — a second pass over transactions the
  rules didn't catch (or to refine categories further), calling a locally
  running model (e.g. via Ollama) so nothing leaves your machine. Writes
  `category_source = 'llm'`.
- **Analytics** — views/queries over `transactions` for spend-by-category,
  income vs. expense trends, net cash flow, year-over-year growth, agent-
  generated summaries.
- **Dashboard** — visualizing the above (candidate: Metabase or Grafana
  pointed at the same Postgres instance, per your stack choice).
- **Forecasting** — a model producing per-category/per-day forecasts,
  written to a forecast "data mart" (its own tables), including deposit
  maturity and credit card payment schedules; surfaced on the dashboard
  with agent commentary.
- **Agents wiring** — the categorization agent, the analytics/summary
  agent, and the forecasting model all reading from and writing to this
  same Postgres DB, with the dashboard as the shared surface.

## Layout

```
selfbudgeting/
  db/
    schema.sql            -- Postgres DDL
    seed_categories.sql   -- starter category tree
  src/selfbudgeting/
    models.py             -- ParsedAccount / ParsedTerms / ParsedTransaction / ParsedStatement
    extract.py             -- PDF -> text (pdfplumber)
    parsers/
      base.py              -- StatementParser interface + version registry
      utils.py             -- number/date parsing helpers
      sber_card_v1.py       -- debit + credit card parser
      sber_savings_deposit_v1.py -- savings + deposit parser
    db.py                  -- upsert logic (accounts, terms, statements, transactions)
    ingest.py               -- CLI: parse a folder of PDFs into the DB
    categorize.py            -- CLI: apply category_rules
  tests/
    fixtures/               -- real statement text excerpts, used to validate parsers
    test_parsers.py
  docker-compose.yml         -- local Postgres
  requirements.txt / pyproject.toml
```
