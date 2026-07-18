-- MVP-2 schema migration: upgrades a 1st-generation database in place.
--
-- Every statement is idempotent, so this file may be replayed safely.
-- Existing rows are preserved: new columns are nullable or carry a DEFAULT,
-- and reason TEXT values are wrapped as {"legacy": "..."} rather than dropped.
--
-- Apply:  psql "$QUANTINUE_DATABASE_URL" -f db/migrations/mvp2.sql

BEGIN;

-- 1. reason TEXT -> JSONB (4 tables). Legacy prose is preserved under "legacy".
DO $$
DECLARE
  target RECORD;
BEGIN
  FOR target IN
    SELECT table_name FROM information_schema.columns
    WHERE table_schema = 'public'
      AND column_name = 'reason'
      AND data_type = 'text'
      AND table_name IN ('tb_disclosure', 'tb_disclosure_signal', 'tb_news', 'tb_news_signal')
  LOOP
    EXECUTE format(
      'ALTER TABLE %I ALTER COLUMN reason TYPE JSONB USING '
      'CASE WHEN reason IS NULL THEN NULL ELSE jsonb_build_object(''legacy'', reason) END',
      target.table_name
    );
  END LOOP;
END $$;

ALTER TABLE tb_disclosure ALTER COLUMN reason SET DEFAULT '{}'::jsonb;
UPDATE tb_disclosure SET reason = '{}'::jsonb WHERE reason IS NULL;

-- 2. Disclosure signal aggregates, mirroring tb_news_signal.
ALTER TABLE tb_disclosure_signal
  ADD COLUMN IF NOT EXISTS disclosure_count SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS top_evidence TEXT[] NOT NULL DEFAULT '{}';

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'tb_disclosure_signal'::regclass
      AND conname = 'tb_disclosure_signal_disclosure_count_check'
  ) THEN
    ALTER TABLE tb_disclosure_signal
      ADD CONSTRAINT tb_disclosure_signal_disclosure_count_check CHECK (disclosure_count >= 0);
  END IF;
END $$;

-- 3. Strategist side admits 'sell' (M5 exits).
ALTER TABLE tb_strategist_signals DROP CONSTRAINT IF EXISTS tb_strategist_signals_side_check;
ALTER TABLE tb_strategist_signals
  ADD CONSTRAINT tb_strategist_signals_side_check CHECK (side IN ('buy', 'hold', 'sell'));

-- 4. Critic cache-state source is renamed so lineage `source` stays uniform.
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'tb_critic_verdict' AND column_name = 'source'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'tb_critic_verdict' AND column_name = 'verdict_source'
  ) THEN
    ALTER TABLE tb_critic_verdict RENAME COLUMN source TO verdict_source;
    ALTER TABLE tb_critic_verdict
      RENAME CONSTRAINT tb_critic_verdict_source_check TO tb_critic_verdict_verdict_source_check;
  END IF;
END $$;

-- 5. Reproduction lineage on roles 07 and 08 (R10). Nullable: existing rows predate it.
ALTER TABLE tb_strategist_signals
  ADD COLUMN IF NOT EXISTS source TEXT,
  ADD COLUMN IF NOT EXISTS source_ref TEXT,
  ADD COLUMN IF NOT EXISTS captured_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS evidence_id TEXT,
  ADD COLUMN IF NOT EXISTS parent_evidence_ids JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS model_provider TEXT,
  ADD COLUMN IF NOT EXISTS model_name TEXT,
  ADD COLUMN IF NOT EXISTS prompt_version TEXT,
  ADD COLUMN IF NOT EXISTS policy_version TEXT,
  ADD COLUMN IF NOT EXISTS input_hash TEXT;

ALTER TABLE tb_critic_verdict
  ADD COLUMN IF NOT EXISTS source TEXT,
  ADD COLUMN IF NOT EXISTS source_ref TEXT,
  ADD COLUMN IF NOT EXISTS captured_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS evidence_id TEXT,
  ADD COLUMN IF NOT EXISTS parent_evidence_ids JSONB NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS model_provider TEXT,
  ADD COLUMN IF NOT EXISTS model_name TEXT,
  ADD COLUMN IF NOT EXISTS prompt_version TEXT,
  ADD COLUMN IF NOT EXISTS policy_version TEXT,
  ADD COLUMN IF NOT EXISTS input_hash TEXT;

-- 6. New tables: users, LLM spend ledger, benchmark closes.
CREATE TABLE IF NOT EXISTS tb_user (
  user_id BIGSERIAL PRIMARY KEY, login_id TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','user')), otp_secret TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tb_llm_usage (
  id BIGSERIAL PRIMARY KEY, called_at TIMESTAMPTZ NOT NULL, task TEXT NOT NULL,
  model TEXT NOT NULL, prompt_tokens INT NOT NULL CHECK (prompt_tokens >= 0),
  completion_tokens INT NOT NULL CHECK (completion_tokens >= 0),
  est_cost_usd NUMERIC NOT NULL CHECK (est_cost_usd >= 0), run_id TEXT
);

CREATE TABLE IF NOT EXISTS tb_benchmark_price (
  price_date DATE NOT NULL, ticker TEXT NOT NULL, close NUMERIC NOT NULL CHECK (close > 0),
  PRIMARY KEY (price_date, ticker)
);

-- 7. Account ownership, investment type, and lifecycle status.
ALTER TABLE tb_account
  ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES tb_user(user_id),
  ADD COLUMN IF NOT EXISTS inv_type TEXT,
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'tb_account'::regclass AND conname = 'tb_account_inv_type_check'
  ) THEN
    ALTER TABLE tb_account ADD CONSTRAINT tb_account_inv_type_check
      CHECK (inv_type IN ('aggressive','conservative'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'tb_account'::regclass AND conname = 'tb_account_status_check'
  ) THEN
    ALTER TABLE tb_account ADD CONSTRAINT tb_account_status_check
      CHECK (status IN ('active','paused','closed'));
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS tb_account_user_id_key
  ON tb_account(user_id) WHERE user_id IS NOT NULL;

COMMIT;
