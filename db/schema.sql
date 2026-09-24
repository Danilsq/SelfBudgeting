-- SelfBudgeting: core schema (Postgres)
-- Stage 1: parsing + storage foundation
--
-- Design notes:
--   * One bank (Sberbank) but four account "shapes": debit_card, credit_card,
--     savings, deposit. Savings & deposit share the same transaction table
--     layout on the statement, but different meta fields (deposit has a
--     fixed term + prolongation events; savings does not).
--   * Bank statement exports overlap in date range every time you re-export
--     (e.g. this week's PDF repeats last week's rows). We never trust the
--     bank to tell us what's new -> every transaction gets a deterministic
--     dedup_hash and we upsert with ON CONFLICT DO NOTHING.
--   * Parsing code will change over time as the bank tweaks its PDF layout.
--     Every statement row records which parser (name + version) produced it,
--     so you can find + reprocess everything a broken parser touched.

CREATE TABLE IF NOT EXISTS accounts (
    account_id       SERIAL PRIMARY KEY,
    account_number   TEXT NOT NULL UNIQUE,          -- normalized bank account number
    account_type     TEXT NOT NULL CHECK (account_type IN ('debit_card','credit_card','savings','deposit')),
    display_name     TEXT,                          -- e.g. "Кредитная СберКарта Молодежная"
    owner_name       TEXT,
    currency         TEXT NOT NULL DEFAULT 'RUB',
    card_mask        TEXT,                          -- last 4 digits, if a card
    opened_date      DATE,
    closed_date      DATE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Terms change over time (rate changes, credit limit changes, prolongations).
-- Keep every observed snapshot instead of overwriting in place.
CREATE TABLE IF NOT EXISTS account_terms (
    term_id             SERIAL PRIMARY KEY,
    account_id          INTEGER NOT NULL REFERENCES accounts(account_id),
    effective_date      DATE NOT NULL,               -- date this snapshot was observed (statement period_end)
    interest_rate       NUMERIC(6,3),
    credit_limit        NUMERIC(14,2),
    grace_period_days   INTEGER,
    term_type           TEXT,                        -- e.g. 'СберВклад', 'Накопительный счёт'
    term_length_days    INTEGER,
    term_end_date        DATE,                        -- original/prolonged maturity date
    contract_date         DATE,
    source_statement_id   INTEGER,
    raw_meta              JSONB,                       -- everything else we parsed but haven't modeled yet
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(account_id, effective_date)
);

CREATE TABLE IF NOT EXISTS statements (
    statement_id     SERIAL PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES accounts(account_id),
    period_start     DATE NOT NULL,
    period_end       DATE NOT NULL,
    opening_balance  NUMERIC(14,2),
    closing_balance  NUMERIC(14,2),
    total_inflow     NUMERIC(14,2),
    total_outflow    NUMERIC(14,2),
    source_file      TEXT NOT NULL,                  -- original PDF filename
    file_hash        TEXT NOT NULL,                  -- sha256 of the PDF bytes
    parser_name      TEXT NOT NULL,
    parser_version   TEXT NOT NULL,
    imported_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(file_hash)
);

CREATE TABLE IF NOT EXISTS categories (
    category_id     SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    parent_id       INTEGER REFERENCES categories(category_id),
    kind            TEXT NOT NULL CHECK (kind IN ('expense','income','transfer','internal'))
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id   BIGSERIAL PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES accounts(account_id),
    statement_id     INTEGER NOT NULL REFERENCES statements(statement_id),
    operation_date   DATE NOT NULL,                  -- when the transaction happened
    processing_date  DATE,                           -- when the bank posted it
    processing_time  TIME,
    auth_code        TEXT,
    raw_category     TEXT,                           -- bank's own category label, if any (cards only)
    description      TEXT NOT NULL,
    amount           NUMERIC(14,2) NOT NULL,          -- signed, in account currency, RUB unless noted
    currency_amount  NUMERIC(14,2),                   -- original amount if txn currency != RUB
    currency         TEXT,
    balance_after    NUMERIC(14,2),
    counter_account  TEXT,                            -- к/с for savings/deposit transfers
    doc_code         TEXT,                             -- "Шифр" operation code (savings/deposit)
    doc_number       TEXT,
    direction        TEXT NOT NULL CHECK (direction IN ('debit','credit')),
    category_id      INTEGER REFERENCES categories(category_id),
    category_source  TEXT CHECK (category_source IN ('bank','rule','llm','manual')),
    dedup_hash       TEXT NOT NULL UNIQUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transactions_account_date ON transactions(account_id, operation_date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_transactions_statement ON transactions(statement_id);

CREATE TABLE IF NOT EXISTS category_rules (
    rule_id       SERIAL PRIMARY KEY,
    pattern       TEXT NOT NULL,                     -- Python regex, case-insensitive
    match_field   TEXT NOT NULL DEFAULT 'description' CHECK (match_field IN ('description','raw_category')),
    category_id   INTEGER NOT NULL REFERENCES categories(category_id),
    priority      INTEGER NOT NULL DEFAULT 100,       -- lower runs first
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    notes         TEXT
);

-- Every parser version that has ever run, for traceability.
CREATE TABLE IF NOT EXISTS parser_registry (
    parser_name     TEXT NOT NULL,
    version         TEXT NOT NULL,
    statement_type  TEXT NOT NULL,
    notes           TEXT,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (parser_name, version)
);
