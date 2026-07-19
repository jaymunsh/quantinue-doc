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

-- M4-7a: 투표원은 technical·disclosure·news·model 4개인데 CHECK가 0~3이라
-- 만장일치 4에서 INSERT가 깨진다. 실계산을 켜기 전에 범위를 넓힌다.
DO $$
BEGIN
  ALTER TABLE tb_strategist_signals
    DROP CONSTRAINT IF EXISTS tb_strategist_signals_signal_consensus_check;
  ALTER TABLE tb_strategist_signals
    ADD CONSTRAINT tb_strategist_signals_signal_consensus_check
    CHECK (signal_consensus BETWEEN 0 AND 4);
END $$;

-- M4 관측: 역할 09의 판단(집행/보류·사유)이 어디에도 저장되지 않아
-- "이번 주에 갭 가드가 몇 번 걸렸나"를 물을 수 없었다. 주문이 생긴 경우만
-- tb_order에 남았을 뿐, 막힌 경우는 JSONB 요약 문자열이 전부였다.
-- 문턱 보정(premarket_gap_max 등)이 바로 이 관측에 의존한다.
CREATE TABLE IF NOT EXISTS tb_order_plan (
  id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL, cycle_ts TIMESTAMPTZ NOT NULL,
  trade_date DATE NOT NULL, account_id BIGINT, signal_id BIGINT,
  decision TEXT NOT NULL CHECK (decision IN ('planned','skipped')), skipped_reason TEXT,
  quantity INT NOT NULL CHECK (quantity >= 0), entry_price NUMERIC, stop_price NUMERIC, take_profit_price NUMERIC,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (ticker, cycle_ts, account_id),
  CHECK ((decision = 'planned' AND skipped_reason IS NULL AND quantity > 0)
      OR (decision = 'skipped' AND skipped_reason IS NOT NULL AND quantity = 0))
);

-- M5: 매도(청산) 주문 표현. tb_order는 브래킷 매수 전용이었다 —
-- order_type CHECK가 'bracket'만 받고, 손절·익절이 NOT NULL이며,
-- stop < entry < take_profit 삼중 제약이 매도에서는 만족될 수 없다.
-- 청산에 더미 손절·익절을 채우는 대신 컬럼을 비우고 제약을 조건부로 만든다.
DO $$
BEGIN
  ALTER TABLE tb_order ALTER COLUMN stop_price DROP NOT NULL;
  ALTER TABLE tb_order ALTER COLUMN take_profit_price DROP NOT NULL;
  ALTER TABLE tb_order ADD COLUMN IF NOT EXISTS closes_order_id BIGINT REFERENCES tb_order(id);

  ALTER TABLE tb_order DROP CONSTRAINT IF EXISTS tb_order_order_type_check;
  ALTER TABLE tb_order ADD CONSTRAINT tb_order_order_type_check
    CHECK (order_type IN ('bracket','close'));

  ALTER TABLE tb_order DROP CONSTRAINT IF EXISTS tb_order_check;
  ALTER TABLE tb_order ADD CONSTRAINT tb_order_check
    CHECK (order_type <> 'bracket' OR (
      stop_price IS NOT NULL AND take_profit_price IS NOT NULL
      AND stop_price < entry_price AND entry_price < take_profit_price));

  ALTER TABLE tb_order DROP CONSTRAINT IF EXISTS tb_order_close_target_check;
  ALTER TABLE tb_order ADD CONSTRAINT tb_order_close_target_check
    CHECK (order_type <> 'close' OR closes_order_id IS NOT NULL);
END $$;


-- Phase 2: 일봉 원장. 신규 테이블이라 무손실 — 기존 행에 손대지 않는다.
CREATE TABLE IF NOT EXISTS tb_daily_bar (
  trade_date DATE NOT NULL, ticker TEXT NOT NULL,
  open NUMERIC NOT NULL CHECK (open > 0), high NUMERIC NOT NULL CHECK (high > 0),
  low NUMERIC NOT NULL CHECK (low > 0), close NUMERIC NOT NULL CHECK (close > 0),
  volume BIGINT NOT NULL CHECK (volume >= 0), source TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (trade_date, ticker),
  CHECK (low <= open AND open <= high), CHECK (low <= close AND close <= high)
);

-- Phase 2: 잡 실행 원장. 신규 테이블이라 무손실 — 기존 행에 손대지 않는다.
CREATE TABLE IF NOT EXISTS tb_job_run (
  job_name TEXT NOT NULL, slot_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed')),
  detail TEXT, started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ,
  PRIMARY KEY (job_name, slot_date),
  CHECK ((status = 'running') = (finished_at IS NULL))
);

-- Phase 2: 공시 원시 원장. 신규 테이블이라 무손실.
CREATE TABLE IF NOT EXISTS tb_disclosure_raw (
  filing_no TEXT NOT NULL, trade_date DATE NOT NULL, ticker TEXT NOT NULL,
  cik TEXT NOT NULL, form_type TEXT NOT NULL, company_name TEXT NOT NULL,
  source_ref TEXT NOT NULL, event_type TEXT, is_hard_event BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (filing_no),
  CHECK (is_hard_event = false OR event_type IS NOT NULL)
);

-- Phase 3: 분석 범위의 크기는 config(screening.llm_depth)와 보유 수가 정한다.
-- 50 상한은 구 스크리너가 종목당 1콜을 쓰던 시절의 흔적이고, 걸리는 순간
-- 보유가 범위 밖으로 밀려 청산 시그널을 남길 자리가 없어진다.
-- 제약 이름은 신규 설치가 생성하는 것과 같아야 한다(카탈로그 대조).
ALTER TABLE tb_daily_pick DROP CONSTRAINT IF EXISTS tb_daily_pick_rank_check;
ALTER TABLE tb_daily_pick ADD CONSTRAINT tb_daily_pick_rank_check CHECK (rank >= 1);

-- Phase 3: 유니버스는 상장 피드가 아니라 거래 가능 범위다. 상장폐지된 보유는
-- 이월되고 여기에 라벨이 붙는다 — 라벨 없이 union만 하면 "왜 상장 피드에 없는
-- 종목이 유니버스에 있나"에 답할 수 없고, 그 자체가 다음 세대의 유령이 된다.
-- 기존 행은 전부 상장 피드에서 온 것이므로 DEFAULT 'listed'가 정확하다.
ALTER TABLE tb_universe ADD COLUMN IF NOT EXISTS listing_status TEXT NOT NULL DEFAULT 'listed';
ALTER TABLE tb_universe DROP CONSTRAINT IF EXISTS tb_universe_listing_status_check;
ALTER TABLE tb_universe ADD CONSTRAINT tb_universe_listing_status_check
  CHECK (listing_status IN ('listed','held_delisted'));

-- Phase 3: 뉴스 원시 원장. 공시(tb_disclosure_raw)와 같은 이유로 FK가 없다 —
-- tb_news(채점 결과)는 (trade_date, ticker) → tb_daily_pick을 걸어 그날 분석
-- 대상이 아닌 종목에 행을 넣을 수 없는데, 일괄 수집이 노리는 것이 그 바깥이다.
-- PK가 (기사, 티커)인 이유: 기사 하나가 여러 종목을 언급하고, 소비는 종목
-- 단위다. 겹치는 창을 다시 받아도 이 키가 중복을 흡수한다.
CREATE TABLE IF NOT EXISTS tb_news_raw (
  article_id BIGINT NOT NULL, ticker TEXT NOT NULL, trade_date DATE NOT NULL,
  headline TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL,
  published_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (article_id, ticker)
);
-- 분석 잡이 매 실행 던지는 유일한 질문의 모양이다: 그 세션 · 이 종목들 ·
-- 최신순 N건. 원장이 하루 1400행씩 자라므로 순차 스캔으로 두면 곧 비싸진다.
CREATE INDEX IF NOT EXISTS ix_news_raw_session ON tb_news_raw (trade_date, ticker, published_at DESC);

-- Phase 4: 당일 시작 equity 스냅샷 — daily_loss_limit의 분모. 소비자는 배분
-- 잡의 계좌 게이트(같은 커밋). 하루 첫 기록이 이긴다 — 잡의 INSERT가
-- ON CONFLICT DO NOTHING이라 재실행이 아침 값을 덮지 않는다.
CREATE TABLE IF NOT EXISTS tb_account_equity_daily (
  account_id BIGINT NOT NULL REFERENCES tb_account(id), trade_date DATE NOT NULL,
  equity NUMERIC NOT NULL CHECK (equity >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (account_id, trade_date)
);
