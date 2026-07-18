-- Quantinue canonical PostgreSQL schema. Logical enums are TEXT + CHECK.
-- All *_at values are UTC TIMESTAMPTZ; clients control display timezone.
CREATE TABLE IF NOT EXISTS tb_universe (
  as_of_date DATE NOT NULL, ticker TEXT NOT NULL, company_name TEXT NOT NULL,
  market_cap BIGINT NOT NULL CHECK (market_cap >= 0), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (as_of_date, ticker)
);
CREATE TABLE IF NOT EXISTS tb_daily_pick (
  trade_date DATE NOT NULL, ticker TEXT NOT NULL, universe_as_of DATE NOT NULL,
  bucket TEXT NOT NULL CHECK (bucket IN ('trend_leader','volume_surge','high_52w_breakout','pullback','squeeze_breakout','backfill')),
  rank INT NOT NULL CHECK (rank BETWEEN 1 AND 50), sector TEXT NOT NULL, score NUMERIC NOT NULL,
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
  confidence NUMERIC NOT NULL CHECK (confidence BETWEEN 0 AND 1), reason TEXT NOT NULL, summary TEXT NOT NULL,
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
  sentiment_score NUMERIC CHECK (sentiment_score BETWEEN 0 AND 1), reason TEXT, is_hard_blocked BOOLEAN NOT NULL DEFAULT false,
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
  confidence NUMERIC CHECK (confidence BETWEEN 0 AND 1), is_confirmed BOOLEAN, reason TEXT, summary TEXT,
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
  disclosure_ref TEXT, reason TEXT, summary TEXT, news_count INT NOT NULL DEFAULT 0 CHECK (news_count >= 0),
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
  side TEXT NOT NULL CHECK (side IN ('buy','hold')), conviction NUMERIC(4,3) NOT NULL CHECK (conviction BETWEEN 0 AND 1),
  signal_consensus SMALLINT NOT NULL CHECK (signal_consensus BETWEEN 0 AND 3), summary TEXT NOT NULL, bull_case TEXT,
  key_risk TEXT, risk_rebuttal TEXT, counter_scenarios JSONB, evidence JSONB NOT NULL, sizing_hint JSONB NOT NULL,
  persona_notes TEXT, decision_close NUMERIC NOT NULL CHECK (decision_close > 0), current_price NUMERIC NOT NULL CHECK (current_price > 0),
  day_high NUMERIC NOT NULL, day_low NUMERIC NOT NULL, close_prev NUMERIC NOT NULL, volume BIGINT NOT NULL,
  turnover NUMERIC NOT NULL, high_52w NUMERIC NOT NULL, low_52w NUMERIC NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (ticker, cycle_ts, inv_type), FOREIGN KEY (trade_date, ticker) REFERENCES tb_daily_pick(trade_date, ticker),
  FOREIGN KEY (ticker, src_disclosure_at) REFERENCES tb_disclosure_signal(ticker, cycle_ts),
  FOREIGN KEY (ticker, src_news_at) REFERENCES tb_news_signal(ticker, cycle_ts), FOREIGN KEY (src_macro_at) REFERENCES tb_macro(as_of)
);
CREATE TABLE IF NOT EXISTS tb_critic_verdict (
  id BIGSERIAL PRIMARY KEY, signal_id BIGINT NOT NULL UNIQUE REFERENCES tb_strategist_signals(id), ticker TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('pass','reject','hold')), is_agreed BOOLEAN, category TEXT NOT NULL,
  objection TEXT NOT NULL, confidence NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  decided_layer TEXT NOT NULL CHECK (decided_layer IN ('quality_gate','hard_rule','llm','gate')),
  source TEXT NOT NULL CHECK (source IN ('fresh','cache','cooldown')), skipped_rules JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tb_account (
  id BIGSERIAL PRIMARY KEY, broker_account_id TEXT NOT NULL UNIQUE, currency TEXT NOT NULL DEFAULT 'USD',
  cash NUMERIC NOT NULL CHECK (cash >= 0), equity NUMERIC NOT NULL CHECK (equity >= 0),
  buying_power NUMERIC NOT NULL CHECK (buying_power >= 0), is_paper BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tb_order (
  id BIGSERIAL PRIMARY KEY, signal_id BIGINT NOT NULL REFERENCES tb_strategist_signals(id),
  account_id BIGINT NOT NULL REFERENCES tb_account(id), ticker TEXT NOT NULL, quantity INT NOT NULL CHECK (quantity > 0),
  entry_price NUMERIC NOT NULL CHECK (entry_price > 0), stop_price NUMERIC NOT NULL CHECK (stop_price > 0),
  take_profit_price NUMERIC NOT NULL CHECK (take_profit_price > 0), order_type TEXT NOT NULL DEFAULT 'bracket' CHECK (order_type IN ('bracket')),
  status TEXT NOT NULL CHECK (status IN ('planned','submitted','filled','failed','canceled')),
  idempotency_key TEXT NOT NULL UNIQUE, broker_order_id TEXT UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  parent_order_id TEXT, stop_leg_order_id TEXT, take_profit_leg_order_id TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (account_id, signal_id), CHECK (stop_price < entry_price AND entry_price < take_profit_price)
);
CREATE TABLE IF NOT EXISTS tb_fill (
  id BIGSERIAL PRIMARY KEY, order_id BIGINT NOT NULL REFERENCES tb_order(id), side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  quantity INT NOT NULL CHECK (quantity > 0), price NUMERIC NOT NULL CHECK (price > 0), filled_at TIMESTAMPTZ NOT NULL,
  broker_fill_id TEXT UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
