-- Guardian persistence: the kernel's journal and the client data it must not lose on restart.
-- Money stays in the bank's core system; these tables hold permissions, evidence and state.

CREATE TABLE IF NOT EXISTS schema_migrations (
    name        text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- One hash chain per kernel run: the kernel keeps its journal in memory and starts a new chain on
-- restart, so old chains stay untouched and every chain verifies on its own.
CREATE TABLE IF NOT EXISTS journal (
    chain        text NOT NULL,
    seq          bigint NOT NULL,
    ts           timestamptz NOT NULL,
    ts_iso       text NOT NULL,           -- the exact string that was hashed
    actor        text NOT NULL,
    event        text NOT NULL,
    data         jsonb NOT NULL,
    stage        smallint,
    passport_id  text,
    prev_hash    char(64) NOT NULL,
    hash         char(64) NOT NULL,
    sig          text NOT NULL,
    PRIMARY KEY (chain, seq)
);

CREATE TABLE IF NOT EXISTS mandates (
    id            text PRIMARY KEY,
    client_id     text NOT NULL,
    mandate_hash  char(64) NOT NULL,
    body          jsonb NOT NULL,          -- the signed mandate (VI format) exactly as the device signed it
    device_alg    text NOT NULL,
    device_sig    text NOT NULL,
    standing      boolean NOT NULL,
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz
);

CREATE TABLE IF NOT EXISTS vault_entries (
    client_id   text NOT NULL,
    name        text NOT NULL,
    domain      text NOT NULL,
    nonce       text NOT NULL,
    ciphertext  text NOT NULL,             -- AES-GCM, AAD = client|name|domain; never plaintext
    remember    boolean NOT NULL,
    created_at  timestamptz NOT NULL,
    PRIMARY KEY (client_id, name)
);

CREATE TABLE IF NOT EXISTS holds (
    id          text PRIMARY KEY,
    client_id   text NOT NULL,
    level       smallint NOT NULL,
    status      text NOT NULL,
    release_at  timestamptz,
    duress      boolean NOT NULL DEFAULT false,
    body        jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id          text PRIMARY KEY,
    client_id   text NOT NULL,
    opened_at   timestamptz NOT NULL,
    body        jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS mandates_client ON mandates (client_id);
CREATE INDEX IF NOT EXISTS holds_client ON holds (client_id);
CREATE INDEX IF NOT EXISTS journal_event ON journal (event);
