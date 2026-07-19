-- Quantinue canonical PostgreSQL schema. Logical enums are TEXT + CHECK.
-- All *_at values are UTC TIMESTAMPTZ; clients control display timezone.
CREATE TABLE IF NOT EXISTS tb_universe (
  as_of_date DATE NOT NULL, ticker TEXT NOT NULL, company_name TEXT NOT NULL,
  market_cap BIGINT NOT NULL CHECK (market_cap >= 0),
  -- 거래 가능 범위는 상장 피드가 아니라 "상장 피드 ∪ 우리가 든 것"이다. 상장이
  -- 폐지된 보유가 여기서 빠지면 tb_daily_pick 행을 못 만들고(FK) → sell 시그널을
  -- 못 남기고 → close 주문을 못 만든다. 팔아야 할 바로 그 종목이 영구히 열린 채
  -- 남는다. 불리언이 아닌 이유: suspended·pending_delisting이 생겨도 CHECK만 늘리면 된다.
  listing_status TEXT NOT NULL DEFAULT 'listed' CHECK (listing_status IN ('listed','held_delisted')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (as_of_date, ticker)
);
CREATE TABLE IF NOT EXISTS tb_daily_pick (
  trade_date DATE NOT NULL, ticker TEXT NOT NULL, universe_as_of DATE NOT NULL,
  bucket TEXT NOT NULL CHECK (bucket IN ('trend_leader','volume_surge','high_52w_breakout','pullback','squeeze_breakout','backfill')),
  -- 상한 없음(의도): 하루의 분석 범위는 상위 screening.llm_depth 에 보유 전부를
  -- 더한 크기라 config와 보유 수가 정한다. 50은 구 스크리너가 종목당 1콜을 쓰던
  -- 시절의 흔적이고, 상한에 걸리면 보유가 범위 밖으로 밀려 청산이 막힌다.
  rank INT NOT NULL CHECK (rank >= 1), sector TEXT NOT NULL, score NUMERIC NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (trade_date, ticker),
  FOREIGN KEY (universe_as_of, ticker) REFERENCES tb_universe(as_of_date, ticker)
);
CREATE TABLE IF NOT EXISTS tb_technical (
  trade_date DATE NOT NULL, ticker TEXT NOT NULL, close NUMERIC NOT NULL CHECK (close > 0),
  rs_20 NUMERIC NOT NULL, vol_ratio NUMERIC NOT NULL, ret_5d NUMERIC NOT NULL,
  ret_20d NUMERIC NOT NULL, atr_pct NUMERIC NOT NULL, high_252_ratio NUMERIC NOT NULL,
  rsi NUMERIC NOT NULL, macd NUMERIC NOT NULL, ma20 NUMERIC NOT NULL, ma50 NUMERIC NOT NULL,
  trend TEXT NOT NULL CHECK (trend IN ('up','mixed','down','no_data')), ml_probs JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (trade_date, ticker),
  FOREIGN KEY (trade_date, ticker) REFERENCES tb_daily_pick(trade_date, ticker)
);
CREATE TABLE IF NOT EXISTS tb_macro (
  as_of TIMESTAMPTZ PRIMARY KEY, regime TEXT NOT NULL CHECK (regime IN ('risk_on','neutral','risk_off')),
  risk_score NUMERIC(4,3) NOT NULL CHECK (risk_score BETWEEN 0 AND 1), vix NUMERIC NOT NULL,
  nasdaq_ret NUMERIC NOT NULL, sp500_ret NUMERIC NOT NULL, rate NUMERIC NOT NULL,
  dollar NUMERIC NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tb_disclosure (
  id BIGSERIAL PRIMARY KEY, ticker TEXT NOT NULL, trade_date DATE NOT NULL, filing_no TEXT NOT NULL UNIQUE,
  form_type TEXT NOT NULL, filing_title TEXT NOT NULL, filed_at TIMESTAMPTZ NOT NULL, event_type TEXT NOT NULL,
  sentiment_score NUMERIC NOT NULL CHECK (sentiment_score BETWEEN 0 AND 1),
  importance NUMERIC NOT NULL CHECK (importance BETWEEN 0 AND 1), risk_score NUMERIC NOT NULL CHECK (risk_score BETWEEN 0 AND 1),
  confidence NUMERIC NOT NULL CHECK (confidence BETWEEN 0 AND 1), reason JSONB NOT NULL DEFAULT '{}', summary TEXT NOT NULL,
  source TEXT NOT NULL, source_ref TEXT NOT NULL, captured_at TIMESTAMPTZ NOT NULL,
  evidence_id TEXT NOT NULL, parent_evidence_ids JSONB NOT NULL DEFAULT '[]', model_provider TEXT NOT NULL,
  model_name TEXT, prompt_version TEXT, policy_version TEXT, input_hash TEXT,
  keywords TEXT[] NOT NULL DEFAULT '{}', permission TEXT NOT NULL CHECK (permission IN ('block','block_buy','trade_eligible')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), FOREIGN KEY (trade_date, ticker) REFERENCES tb_daily_pick(trade_date, ticker)
);
CREATE TABLE IF NOT EXISTS tb_disclosure_signal (
  tds_id BIGSERIAL PRIMARY KEY, cycle_ts TIMESTAMPTZ NOT NULL, ticker TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), trade_date DATE NOT NULL, has_signal BOOLEAN NOT NULL,
  filing_title TEXT, filing_no TEXT, filed_at TIMESTAMPTZ, event_type TEXT,
  importance NUMERIC CHECK (importance BETWEEN 0 AND 1), risk_score NUMERIC CHECK (risk_score BETWEEN 0 AND 1),
  sentiment_score NUMERIC CHECK (sentiment_score BETWEEN 0 AND 1), reason JSONB, is_hard_blocked BOOLEAN NOT NULL DEFAULT false,
  disclosure_count SMALLINT NOT NULL DEFAULT 0 CHECK (disclosure_count >= 0), top_evidence TEXT[] NOT NULL DEFAULT '{}',
  hard_block_reason TEXT, confidence NUMERIC CHECK (confidence BETWEEN 0 AND 1), summary TEXT,
  source TEXT NOT NULL, source_ref TEXT NOT NULL, captured_at TIMESTAMPTZ NOT NULL,
  evidence_id TEXT NOT NULL, parent_evidence_ids JSONB NOT NULL DEFAULT '[]', model_provider TEXT NOT NULL,
  model_name TEXT, prompt_version TEXT, policy_version TEXT, input_hash TEXT,
  UNIQUE (ticker, cycle_ts), FOREIGN KEY (trade_date, ticker) REFERENCES tb_daily_pick(trade_date, ticker),
  FOREIGN KEY (filing_no) REFERENCES tb_disclosure(filing_no)
);
CREATE TABLE IF NOT EXISTS tb_news (
  id BIGSERIAL PRIMARY KEY, ticker TEXT NOT NULL, trade_date DATE NOT NULL, news_key TEXT NOT NULL,
  title TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL, published_at TIMESTAMPTZ NOT NULL,
  grade TEXT NOT NULL CHECK (grade IN ('allow','gray','block')), is_dropped BOOLEAN NOT NULL, drop_reason TEXT,
  event_type TEXT, sentiment_score NUMERIC CHECK (sentiment_score BETWEEN 0 AND 1), importance NUMERIC CHECK (importance BETWEEN 0 AND 1),
  risk_score NUMERIC CHECK (risk_score BETWEEN 0 AND 1), source_trust NUMERIC CHECK (source_trust BETWEEN 0 AND 1),
  confidence NUMERIC CHECK (confidence BETWEEN 0 AND 1), is_confirmed BOOLEAN, reason JSONB, summary TEXT,
  source_ref TEXT NOT NULL, captured_at TIMESTAMPTZ NOT NULL, evidence_id TEXT NOT NULL,
  parent_evidence_ids JSONB NOT NULL DEFAULT '[]', model_provider TEXT NOT NULL,
  model_name TEXT, prompt_version TEXT, policy_version TEXT, input_hash TEXT,
  keywords TEXT[] NOT NULL DEFAULT '{}', permission TEXT CHECK (permission IN ('block','block_buy','trade_eligible')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (news_key, ticker),
  FOREIGN KEY (trade_date, ticker) REFERENCES tb_daily_pick(trade_date, ticker)
);
CREATE TABLE IF NOT EXISTS tb_news_signal (
  tns_id BIGSERIAL PRIMARY KEY, cycle_ts TIMESTAMPTZ NOT NULL, ticker TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), trade_date DATE NOT NULL, has_signal BOOLEAN NOT NULL,
  rep_news_id BIGINT, news_title TEXT, source TEXT, published_at TIMESTAMPTZ, ref TEXT, event_type TEXT,
  disclosure_ref TEXT, reason JSONB, summary TEXT, news_count INT NOT NULL DEFAULT 0 CHECK (news_count >= 0),
  importance NUMERIC CHECK (importance BETWEEN 0 AND 1), peak_importance NUMERIC CHECK (peak_importance BETWEEN 0 AND 1),
  risk_score NUMERIC CHECK (risk_score BETWEEN 0 AND 1), sentiment_score NUMERIC CHECK (sentiment_score BETWEEN 0 AND 1),
  source_trust NUMERIC CHECK (source_trust BETWEEN 0 AND 1), grade_score NUMERIC CHECK (grade_score BETWEEN 0 AND 1),
  confidence NUMERIC CHECK (confidence BETWEEN 0 AND 1), is_hard_blocked BOOLEAN NOT NULL DEFAULT false,
  hard_block_reason TEXT, top_evidence TEXT[] NOT NULL DEFAULT '{}', UNIQUE (ticker, cycle_ts),
  source_ref TEXT NOT NULL, captured_at TIMESTAMPTZ NOT NULL, evidence_id TEXT NOT NULL,
  parent_evidence_ids JSONB NOT NULL DEFAULT '[]', model_provider TEXT NOT NULL,
  model_name TEXT, prompt_version TEXT, policy_version TEXT, input_hash TEXT,
  FOREIGN KEY (trade_date, ticker) REFERENCES tb_daily_pick(trade_date, ticker), FOREIGN KEY (rep_news_id) REFERENCES tb_news(id),
  FOREIGN KEY (disclosure_ref) REFERENCES tb_disclosure(filing_no)
);
CREATE TABLE IF NOT EXISTS tb_strategist_signals (
  id BIGSERIAL PRIMARY KEY, trade_date DATE NOT NULL, ticker TEXT NOT NULL, cycle_ts TIMESTAMPTZ NOT NULL,
  src_disclosure_at TIMESTAMPTZ, src_news_at TIMESTAMPTZ, src_macro_at TIMESTAMPTZ,
  inv_type TEXT NOT NULL CHECK (inv_type IN ('aggressive','conservative')),
  side TEXT NOT NULL CHECK (side IN ('buy','hold','sell')), conviction NUMERIC(4,3) NOT NULL CHECK (conviction BETWEEN 0 AND 1),
  signal_consensus SMALLINT NOT NULL CHECK (signal_consensus BETWEEN 0 AND 4), summary TEXT NOT NULL, bull_case TEXT,
  key_risk TEXT, risk_rebuttal TEXT, counter_scenarios JSONB, evidence JSONB NOT NULL, sizing_hint JSONB NOT NULL,
  persona_notes TEXT, decision_close NUMERIC NOT NULL CHECK (decision_close > 0), current_price NUMERIC NOT NULL CHECK (current_price > 0),
  day_high NUMERIC NOT NULL, day_low NUMERIC NOT NULL, close_prev NUMERIC NOT NULL, volume BIGINT NOT NULL,
  turnover NUMERIC NOT NULL, high_52w NUMERIC NOT NULL, low_52w NUMERIC NOT NULL,
  source TEXT, source_ref TEXT, captured_at TIMESTAMPTZ, evidence_id TEXT,
  parent_evidence_ids JSONB NOT NULL DEFAULT '[]', model_provider TEXT, model_name TEXT,
  prompt_version TEXT, policy_version TEXT, input_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (ticker, cycle_ts, inv_type), FOREIGN KEY (trade_date, ticker) REFERENCES tb_daily_pick(trade_date, ticker),
  FOREIGN KEY (ticker, src_disclosure_at) REFERENCES tb_disclosure_signal(ticker, cycle_ts),
  FOREIGN KEY (ticker, src_news_at) REFERENCES tb_news_signal(ticker, cycle_ts), FOREIGN KEY (src_macro_at) REFERENCES tb_macro(as_of)
);
CREATE TABLE IF NOT EXISTS tb_critic_verdict (
  id BIGSERIAL PRIMARY KEY, signal_id BIGINT NOT NULL UNIQUE REFERENCES tb_strategist_signals(id), ticker TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('pass','reject','hold')), is_agreed BOOLEAN, category TEXT NOT NULL,
  objection TEXT NOT NULL, confidence NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  decided_layer TEXT NOT NULL CHECK (decided_layer IN ('quality_gate','hard_rule','llm','gate')),
  verdict_source TEXT NOT NULL CHECK (verdict_source IN ('fresh','cache','cooldown')),
  skipped_rules JSONB NOT NULL DEFAULT '[]',
  source TEXT, source_ref TEXT, captured_at TIMESTAMPTZ, evidence_id TEXT,
  parent_evidence_ids JSONB NOT NULL DEFAULT '[]', model_provider TEXT, model_name TEXT,
  prompt_version TEXT, policy_version TEXT, input_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tb_user (
  user_id BIGSERIAL PRIMARY KEY, login_id TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','user')), otp_secret TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tb_account (
  id BIGSERIAL PRIMARY KEY, broker_account_id TEXT NOT NULL UNIQUE, currency TEXT NOT NULL DEFAULT 'USD',
  cash NUMERIC NOT NULL CHECK (cash >= 0), equity NUMERIC NOT NULL CHECK (equity >= 0),
  buying_power NUMERIC NOT NULL CHECK (buying_power >= 0), is_paper BOOLEAN NOT NULL DEFAULT true,
  user_id BIGINT REFERENCES tb_user(user_id),
  inv_type TEXT CHECK (inv_type IN ('aggressive','conservative')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','closed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tb_order (
  id BIGSERIAL PRIMARY KEY, signal_id BIGINT NOT NULL REFERENCES tb_strategist_signals(id),
  account_id BIGINT NOT NULL REFERENCES tb_account(id), ticker TEXT NOT NULL, quantity INT NOT NULL CHECK (quantity > 0),
  entry_price NUMERIC NOT NULL CHECK (entry_price > 0), stop_price NUMERIC CHECK (stop_price > 0),
  take_profit_price NUMERIC CHECK (take_profit_price > 0), order_type TEXT NOT NULL DEFAULT 'bracket' CHECK (order_type IN ('bracket','close')),
  closes_order_id BIGINT REFERENCES tb_order(id),
  status TEXT NOT NULL CHECK (status IN ('planned','submitted','filled','failed','canceled')),
  idempotency_key TEXT NOT NULL UNIQUE, broker_order_id TEXT UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  parent_order_id TEXT, stop_leg_order_id TEXT, take_profit_leg_order_id TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (account_id, signal_id),
  -- 두 제약에 이름을 명시하는 이유: 익명 CHECK는 Postgres가 tb_order_check,
  -- tb_order_check1처럼 **선언 순서로** 이름을 짓는다. 마이그레이션은
  -- ALTER ... ADD CONSTRAINT라 이름을 직접 대므로, 신규 설치와 마이그레이션의
  -- 카탈로그가 정의는 같은데 이름만 갈렸다. 순서에 기대지 않게 못 박는다.
  --
  -- 브래킷 삼중 제약은 매수에만 해당한다. 청산에는 손절·익절이 없으므로
  -- 더미 값을 채우는 대신 컬럼을 비운다.
  CONSTRAINT tb_order_check CHECK (order_type <> 'bracket' OR (
    stop_price IS NOT NULL AND take_profit_price IS NOT NULL
    AND stop_price < entry_price AND entry_price < take_profit_price)),
  -- 청산은 반드시 어느 매수를 닫는지 가리켜야 한다(실현손익의 짝).
  CONSTRAINT tb_order_close_target_check
    CHECK (order_type <> 'close' OR closes_order_id IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS tb_fill (
  id BIGSERIAL PRIMARY KEY, order_id BIGINT NOT NULL REFERENCES tb_order(id), side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  quantity INT NOT NULL CHECK (quantity > 0), price NUMERIC NOT NULL CHECK (price > 0), filled_at TIMESTAMPTZ NOT NULL,
  broker_fill_id TEXT UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Phase 4: 당일 시작 equity 스냅샷 — daily_loss_limit의 분모.
-- 하루 첫 기록이 이긴다(잡의 INSERT는 ON CONFLICT DO NOTHING). 재실행이
-- 아침 값을 덮으면 '당일 시작 대비'라는 정의 자체가 거짓이 된다.
CREATE TABLE IF NOT EXISTS tb_account_equity_daily (
  account_id BIGINT NOT NULL REFERENCES tb_account(id), trade_date DATE NOT NULL,
  equity NUMERIC NOT NULL CHECK (equity >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (account_id, trade_date)
);
CREATE TABLE IF NOT EXISTS tb_review_price_snapshots (
  signal_id BIGINT NOT NULL REFERENCES tb_strategist_signals(id), day_offset SMALLINT NOT NULL CHECK (day_offset BETWEEN 1 AND 5),
  price_date DATE NOT NULL, close NUMERIC NOT NULL CHECK (close > 0), source TEXT NOT NULL CHECK (source IN ('fixture','market_data')),
  source_ref TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL, captured_at TIMESTAMPTZ NOT NULL,
  confidence NUMERIC NOT NULL CHECK (confidence BETWEEN 0 AND 1), evidence_id TEXT NOT NULL,
  parent_evidence_ids JSONB NOT NULL DEFAULT '[]',
  model_provider TEXT, model_name TEXT, prompt_version TEXT, policy_version TEXT, input_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (signal_id, day_offset)
);
CREATE TABLE IF NOT EXISTS tb_review (
  signal_id BIGINT PRIMARY KEY REFERENCES tb_strategist_signals(id), ret_1d NUMERIC NOT NULL, ret_3d NUMERIC NOT NULL,
  ret_5d NUMERIC NOT NULL, is_hit BOOLEAN NOT NULL, max_drawdown NUMERIC NOT NULL CHECK (max_drawdown <= 0),
  lesson TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Operational tables are execution logs, never domain FK parents.
CREATE TABLE IF NOT EXISTS tb_order_plan (
  id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL, cycle_ts TIMESTAMPTZ NOT NULL,
  trade_date DATE NOT NULL, account_id BIGINT, signal_id BIGINT,
  decision TEXT NOT NULL CHECK (decision IN ('planned','skipped')), skipped_reason TEXT,
  quantity INT NOT NULL CHECK (quantity >= 0), entry_price NUMERIC, stop_price NUMERIC, take_profit_price NUMERIC,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (ticker, cycle_ts, account_id),
  CHECK ((decision = 'planned' AND skipped_reason IS NULL AND quantity > 0)
      OR (decision = 'skipped' AND skipped_reason IS NOT NULL AND quantity = 0))
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, ticker TEXT NOT NULL,
  cycle_ts TIMESTAMPTZ NOT NULL, status TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','timed_out')),
  payload JSONB NOT NULL DEFAULT '{}', started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE pipeline_runs ALTER COLUMN payload TYPE JSONB USING payload::JSONB;
UPDATE pipeline_runs
SET status = COALESCE(payload->>'status', 'completed')
WHERE status IS NULL;
ALTER TABLE pipeline_runs ALTER COLUMN status SET NOT NULL;
CREATE TABLE IF NOT EXISTS pipeline_stage_attempts (
  attempt_id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id), component TEXT NOT NULL,
  attempt_no INT NOT NULL CHECK (attempt_no > 0), status TEXT NOT NULL CHECK (status IN ('pending','running','retrying','completed','failed','timed_out')),
  started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ, error_code TEXT, error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (run_id, component, attempt_no)
);
CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
  checkpoint_id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id), component TEXT NOT NULL,
  payload JSONB NOT NULL, payload_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (run_id, component)
);
CREATE TABLE IF NOT EXISTS order_submissions (
  submission_id BIGSERIAL PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK (state IN ('claimed','submitted','completed','failed')),
  owner_token TEXT NOT NULL, claimed_at TIMESTAMPTZ NOT NULL, stale_after TIMESTAMPTZ NOT NULL,
  run_id TEXT REFERENCES pipeline_runs(run_id), order_id BIGINT REFERENCES tb_order(id),
  broker_order_id TEXT, result_payload JSONB, last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (stale_after > claimed_at)
);
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_ticker_cycle ON pipeline_runs (ticker, cycle_ts DESC);

CREATE UNIQUE INDEX IF NOT EXISTS tb_account_user_id_key ON tb_account(user_id) WHERE user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS tb_llm_usage (
  id BIGSERIAL PRIMARY KEY, called_at TIMESTAMPTZ NOT NULL, task TEXT NOT NULL,
  model TEXT NOT NULL, prompt_tokens INT NOT NULL CHECK (prompt_tokens >= 0),
  completion_tokens INT NOT NULL CHECK (completion_tokens >= 0),
  est_cost_usd NUMERIC NOT NULL CHECK (est_cost_usd >= 0), run_id TEXT
);

-- 일봉 원장. 스크리닝 랭킹·브래킷 발동 판정·시가평가가 전부 여기서 온다.
-- 종목당 1콜로 받던 것을 배치 1콜로 바꾸는 근거이자, 같은 날을 두 번 받아도
-- 값이 흔들리지 않게 하는 자리다(PK로 하루 1행 고정).
CREATE TABLE IF NOT EXISTS tb_daily_bar (
  trade_date DATE NOT NULL, ticker TEXT NOT NULL,
  open NUMERIC NOT NULL CHECK (open > 0), high NUMERIC NOT NULL CHECK (high > 0),
  low NUMERIC NOT NULL CHECK (low > 0), close NUMERIC NOT NULL CHECK (close > 0),
  volume BIGINT NOT NULL CHECK (volume >= 0), source TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (trade_date, ticker),
  CHECK (low <= open AND open <= high), CHECK (low <= close AND close <= high)
);

CREATE TABLE IF NOT EXISTS tb_benchmark_price (
  price_date DATE NOT NULL, ticker TEXT NOT NULL, close NUMERIC NOT NULL CHECK (close > 0),
  PRIMARY KEY (price_date, ticker)
);

-- 잡 실행 원장. PK가 "잡 하나는 하루 한 번"을 DB 수준에서 강제한다 —
-- 스케줄러가 60초마다 깨어나도 잡 본문은 예약에 성공한 틱에서만 돈다.
-- 예약(running)과 성공(succeeded)을 구분하는 이유: 예약만 하고 죽은 실행을
-- 성공으로 세면 그 주기를 통째로 잃는다(주간 잡이면 한 주).
CREATE TABLE IF NOT EXISTS tb_job_run (
  job_name TEXT NOT NULL, slot_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed')),
  detail TEXT, started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ,
  PRIMARY KEY (job_name, slot_date),
  CHECK ((status = 'running') = (finished_at IS NULL))
);

-- 공시 원시 원장. tb_disclosure(채점 결과)와 따로 두는 이유는 FK다 — 그쪽은
-- (trade_date, ticker) → tb_daily_pick을 걸어 그날 분석 대상이 아닌 종목에는
-- 행을 넣을 수 없는데, 일괄 수집이 노리는 것이 정확히 그 바깥이다(스크리너에서
-- 탈락한 보유 종목의 상장폐지 공시). 그래서 여기엔 FK를 걸지 않는다.
CREATE TABLE IF NOT EXISTS tb_disclosure_raw (
  filing_no TEXT NOT NULL, trade_date DATE NOT NULL, ticker TEXT NOT NULL,
  cik TEXT NOT NULL, form_type TEXT NOT NULL, company_name TEXT NOT NULL,
  source_ref TEXT NOT NULL, event_type TEXT, is_hard_event BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (filing_no),
  CHECK (is_hard_event = false OR event_type IS NOT NULL)
);

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
