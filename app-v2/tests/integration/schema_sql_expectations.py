"""Expected PostgreSQL catalog identities and semantic constraints."""

from pathlib import Path
from typing import TypeAlias

ForeignKey: TypeAlias = tuple[tuple[str, ...], str, tuple[str, ...]]

SCHEMA = Path("db/schema.sql").resolve()
TABLES = {
    "tb_universe",
    "tb_daily_pick",
    "tb_technical",
    "tb_macro",
    "tb_disclosure",
    "tb_disclosure_signal",
    "tb_news",
    "tb_news_signal",
    "tb_strategist_signals",
    "tb_critic_verdict",
    "tb_account",
    "tb_order",
    "tb_fill",
    "tb_review_price_snapshots",
    "tb_review",
    "pipeline_runs",
    "pipeline_stage_attempts",
    "pipeline_checkpoints",
    "order_submissions",
    "tb_user",
    "tb_llm_usage",
    "tb_benchmark_price",
    "tb_daily_bar",
    "tb_job_run",
    "tb_disclosure_raw",
    "tb_news_raw",
    "tb_order_plan",
    "tb_account_equity_daily",
}
PK = {
    "tb_universe": ("as_of_date", "ticker"),
    "tb_daily_pick": ("trade_date", "ticker"),
    "tb_technical": ("trade_date", "ticker"),
    "tb_macro": ("as_of",),
    "tb_disclosure": ("id",),
    "tb_disclosure_signal": ("tds_id",),
    "tb_news": ("id",),
    "tb_news_signal": ("tns_id",),
    "tb_strategist_signals": ("id",),
    "tb_critic_verdict": ("id",),
    "tb_account": ("id",),
    "tb_order": ("id",),
    "tb_fill": ("id",),
    "tb_review_price_snapshots": ("signal_id", "day_offset"),
    "tb_review": ("signal_id",),
    "tb_order_plan": ("id",),
    "pipeline_runs": ("run_id",),
    "pipeline_stage_attempts": ("attempt_id",),
    "pipeline_checkpoints": ("checkpoint_id",),
    "order_submissions": ("submission_id",),
    "tb_user": ("user_id",),
    "tb_llm_usage": ("id",),
    "tb_benchmark_price": ("price_date", "ticker"),
    "tb_daily_bar": ("trade_date", "ticker"),
    "tb_job_run": ("job_name", "slot_date"),
    "tb_disclosure_raw": ("filing_no",),
    # 기사 하나가 여러 종목을 언급하므로 기사 id만으로는 행을 못 가른다.
    "tb_news_raw": ("article_id", "ticker"),
    # 당일 시작 equity는 계좌·날짜당 하나다 — 첫 기록이 이긴다.
    "tb_account_equity_daily": ("account_id", "trade_date"),
}
UNIQUE = {
    "tb_disclosure": {("filing_no",)},
    "tb_disclosure_signal": {("ticker", "cycle_ts")},
    "tb_news": {("news_key", "ticker")},
    "tb_news_signal": {("ticker", "cycle_ts")},
    "tb_strategist_signals": {("ticker", "cycle_ts", "inv_type")},
    "tb_critic_verdict": {("signal_id",)},
    "tb_account": {("broker_account_id",)},
    "tb_user": {("login_id",)},
    "tb_order_plan": {("ticker", "cycle_ts", "account_id")},
    "tb_order": {
        ("broker_order_id",),
        ("idempotency_key",),
        ("account_id", "signal_id"),
    },
    "tb_fill": {("broker_fill_id",)},
    "pipeline_runs": {("idempotency_key",)},
    "pipeline_stage_attempts": {("run_id", "component", "attempt_no")},
    "pipeline_checkpoints": {("run_id", "component")},
    "order_submissions": {("client_order_id",)},
}

ORDER_LEG_COLUMNS = {
    "parent_order_id",
    "stop_leg_order_id",
    "take_profit_leg_order_id",
}

PROVENANCE_COLUMNS = {
    "tb_disclosure": {
        ("source", "NO"),
        ("source_ref", "NO"),
        ("captured_at", "NO"),
        ("evidence_id", "NO"),
        ("parent_evidence_ids", "NO"),
        ("model_provider", "NO"),
        ("model_name", "YES"),
        ("prompt_version", "YES"),
        ("policy_version", "YES"),
        ("input_hash", "YES"),
    },
    "tb_disclosure_signal": {
        ("source", "NO"),
        ("source_ref", "NO"),
        ("captured_at", "NO"),
        ("evidence_id", "NO"),
        ("parent_evidence_ids", "NO"),
        ("model_provider", "NO"),
        ("model_name", "YES"),
        ("prompt_version", "YES"),
        ("policy_version", "YES"),
        ("input_hash", "YES"),
    },
    "tb_news": {
        ("source", "NO"),
        ("source_ref", "NO"),
        ("captured_at", "NO"),
        ("evidence_id", "NO"),
        ("parent_evidence_ids", "NO"),
        ("model_provider", "NO"),
        ("model_name", "YES"),
        ("prompt_version", "YES"),
        ("policy_version", "YES"),
        ("input_hash", "YES"),
    },
    "tb_news_signal": {
        ("source_ref", "NO"),
        ("captured_at", "NO"),
        ("evidence_id", "NO"),
        ("parent_evidence_ids", "NO"),
        ("model_provider", "NO"),
        ("model_name", "YES"),
        ("prompt_version", "YES"),
        ("policy_version", "YES"),
        ("input_hash", "YES"),
    },
    "tb_review_price_snapshots": {
        ("source_ref", "NO"),
        ("observed_at", "NO"),
        ("captured_at", "NO"),
        ("confidence", "NO"),
        ("evidence_id", "NO"),
        ("parent_evidence_ids", "NO"),
        ("model_provider", "YES"),
        ("model_name", "YES"),
        ("prompt_version", "YES"),
        ("policy_version", "YES"),
        ("input_hash", "YES"),
    },
}
FK: dict[str, set[ForeignKey]] = {
    "tb_daily_pick": {(("universe_as_of", "ticker"), "tb_universe", ("as_of_date", "ticker"))},
    "tb_technical": {(("trade_date", "ticker"), "tb_daily_pick", ("trade_date", "ticker"))},
    "tb_disclosure": {(("trade_date", "ticker"), "tb_daily_pick", ("trade_date", "ticker"))},
    "tb_disclosure_signal": {
        (("trade_date", "ticker"), "tb_daily_pick", ("trade_date", "ticker")),
        (("filing_no",), "tb_disclosure", ("filing_no",)),
    },
    "tb_news": {(("trade_date", "ticker"), "tb_daily_pick", ("trade_date", "ticker"))},
    "tb_news_signal": {
        (("trade_date", "ticker"), "tb_daily_pick", ("trade_date", "ticker")),
        (("rep_news_id",), "tb_news", ("id",)),
        (("disclosure_ref",), "tb_disclosure", ("filing_no",)),
    },
    "tb_strategist_signals": {
        (("trade_date", "ticker"), "tb_daily_pick", ("trade_date", "ticker")),
        (("ticker", "src_disclosure_at"), "tb_disclosure_signal", ("ticker", "cycle_ts")),
        (("ticker", "src_news_at"), "tb_news_signal", ("ticker", "cycle_ts")),
        (("src_macro_at",), "tb_macro", ("as_of",)),
    },
    "tb_critic_verdict": {(("signal_id",), "tb_strategist_signals", ("id",))},
    "tb_account": {(("user_id",), "tb_user", ("user_id",))},
    "tb_account_equity_daily": {(("account_id",), "tb_account", ("id",))},
    "tb_order": {
        (("signal_id",), "tb_strategist_signals", ("id",)),
        (("account_id",), "tb_account", ("id",)),
        # 청산이 어느 매수를 닫는지 — 실현손익의 짝.
        (("closes_order_id",), "tb_order", ("id",)),
    },
    "tb_fill": {(("order_id",), "tb_order", ("id",))},
    "tb_review_price_snapshots": {(("signal_id",), "tb_strategist_signals", ("id",))},
    "tb_review": {(("signal_id",), "tb_strategist_signals", ("id",))},
    "pipeline_stage_attempts": {(("run_id",), "pipeline_runs", ("run_id",))},
    "pipeline_checkpoints": {(("run_id",), "pipeline_runs", ("run_id",))},
    "order_submissions": {
        (("run_id",), "pipeline_runs", ("run_id",)),
        (("order_id",), "tb_order", ("id",)),
    },
}
# Each tuple is one CHECK constraint and lists every semantic fragment it must contain.
CHECKS = {
    # NUMERIC 캐스트가 붙어 카탈로그에는 (0)::numeric으로 남는다 — 접두만 본다.
    "tb_account_equity_daily": {("equity",): ("equity >=",)},
    "tb_order_plan": {
        ("decision",): ("'planned'", "'skipped'"),
        ("quantity",): ("quantity >= 0",),
        # 집행이면 사유가 없고 수량이 있다; 보류면 사유가 있고 수량이 0이다.
        ("decision", "skipped_reason", "quantity"): (
            "skipped_reason is null",
            "skipped_reason is not null",
            "quantity > 0",
            "quantity = 0",
        ),
    },
    "tb_universe": {
        ("market_cap",): ("market_cap >= 0",),
        # 거래 가능 범위 = 상장 피드 더하기 보유. 이월분에는 라벨이 붙는다.
        ("listing_status",): ("'listed'", "'held_delisted'"),
    },
    "tb_daily_pick": {
        ("bucket",): ("trend_leader", "backfill"),
        # 상한 없음: 범위 크기는 screening.llm_depth + 보유 수가 정한다.
        ("rank",): ("rank >= 1",),
    },
    "tb_technical": {("close",): ("close >",), ("trend",): ("'up'", "'no_data'")},
    "tb_daily_bar": {
        ("open",): ("open >",),
        ("high",): ("high >",),
        ("low",): ("low >",),
        ("close",): ("close >",),
        ("volume",): ("volume >=",),
        # 봉의 내적 정합성 — 시가·종가가 저가와 고가 사이에 있어야 한다.
        # 이게 깨진 봉은 브래킷 발동 판정을 거짓으로 만든다.
        ("low", "open", "high"): ("low <= open", "open <= high"),
        ("low", "close", "high"): ("low <= close", "close <= high"),
    },
    "tb_disclosure_raw": {
        # 하드 이벤트라면 어떤 이벤트인지 반드시 있어야 한다 — 근거 없는 즉시
        # 청산을 원장 수준에서 막는다.
        ("is_hard_event", "event_type"): ("is_hard_event", "event_type is not null"),
    },
    "tb_job_run": {
        ("status",): ("'running'", "'succeeded'", "'failed'"),
        # 상태와 종료시각이 어긋난 행을 만들 수 없게 묶는다 — running인데
        # 끝난 시각이 있거나, 끝났는데 없는 행은 주기 판정을 거짓으로 만든다.
        ("status", "finished_at"): ("'running'", "finished_at is null"),
    },
    "tb_macro": {
        ("regime",): ("'risk_on'", "'risk_off'"),
        ("risk_score",): ("risk_score >=", "risk_score <="),
    },
    "tb_disclosure": {
        ("sentiment_score",): ("sentiment_score >=", "sentiment_score <="),
        ("importance",): ("importance >=", "importance <="),
        ("risk_score",): ("risk_score >=", "risk_score <="),
        ("confidence",): ("confidence >=", "confidence <="),
        ("permission",): ("'block'", "'block_buy'", "'trade_eligible'"),
    },
    "tb_disclosure_signal": {
        ("importance",): ("importance >=", "importance <="),
        ("risk_score",): ("risk_score >=", "risk_score <="),
        ("sentiment_score",): ("sentiment_score >=", "sentiment_score <="),
        ("confidence",): ("confidence >=", "confidence <="),
        ("disclosure_count",): ("disclosure_count >=",),
    },
    "tb_news": {
        ("grade",): ("'allow'", "'gray'", "'block'"),
        ("sentiment_score",): ("sentiment_score >=", "sentiment_score <="),
        ("importance",): ("importance >=", "importance <="),
        ("risk_score",): ("risk_score >=", "risk_score <="),
        ("source_trust",): ("source_trust >=", "source_trust <="),
        ("confidence",): ("confidence >=", "confidence <="),
        ("permission",): ("'block'", "'block_buy'", "'trade_eligible'"),
    },
    "tb_news_signal": {
        ("news_count",): ("news_count >= 0",),
        ("importance",): ("importance >=", "importance <="),
        ("peak_importance",): ("peak_importance >=", "peak_importance <="),
        ("risk_score",): ("risk_score >=", "risk_score <="),
        ("sentiment_score",): ("sentiment_score >=", "sentiment_score <="),
        ("source_trust",): ("source_trust >=", "source_trust <="),
        ("grade_score",): ("grade_score >=", "grade_score <="),
        ("confidence",): ("confidence >=", "confidence <="),
    },
    "tb_strategist_signals": {
        ("inv_type",): ("'aggressive'", "'conservative'"),
        ("side",): ("'buy'", "'hold'"),
        ("conviction",): ("conviction >=", "conviction <="),
        ("signal_consensus",): ("signal_consensus >= 0", "signal_consensus <= 4"),
        ("decision_close",): ("decision_close >",),
        ("current_price",): ("current_price >",),
    },
    "tb_critic_verdict": {
        ("decision",): ("'pass'", "'reject'", "'hold'"),
        ("confidence",): ("confidence >=", "confidence <="),
        ("decided_layer",): ("'quality_gate'", "'hard_rule'", "'llm'", "'gate'"),
        ("verdict_source",): ("'fresh'", "'cache'", "'cooldown'"),
    },
    "tb_user": {
        ("role",): ("'admin'", "'user'"),
    },
    "tb_llm_usage": {
        ("prompt_tokens",): ("prompt_tokens >=",),
        ("completion_tokens",): ("completion_tokens >=",),
        ("est_cost_usd",): ("est_cost_usd >=",),
    },
    "tb_benchmark_price": {
        ("close",): ("close >",),
    },
    "tb_account": {
        ("cash",): ("cash >=",),
        ("equity",): ("equity >=",),
        ("buying_power",): ("buying_power >=",),
        ("inv_type",): ("'aggressive'", "'conservative'"),
        ("status",): ("'active'", "'paused'", "'closed'"),
    },
    "tb_order": {
        ("quantity",): ("quantity > 0",),
        ("entry_price",): ("entry_price >",),
        ("stop_price",): ("stop_price >",),
        ("take_profit_price",): ("take_profit_price >",),
        ("order_type",): ("'bracket'", "'close'"),
        ("status",): ("'planned'", "'submitted'", "'filled'", "'failed'", "'canceled'"),
        # 브래킷 삼중 제약은 매수에만 — 청산엔 손절·익절이 없다.
        ("order_type", "stop_price", "take_profit_price", "entry_price"): (
            "order_type <> 'bracket'",
            "stop_price < entry_price",
            "entry_price < take_profit_price",
        ),
        # 청산은 반드시 어느 매수를 닫는지 가리킨다.
        ("order_type", "closes_order_id"): (
            "order_type <> 'close'",
            "closes_order_id is not null",
        ),
    },
    "tb_fill": {
        ("side",): ("'buy'", "'sell'"),
        ("quantity",): ("quantity > 0",),
        ("price",): ("price >",),
    },
    "tb_review_price_snapshots": {
        ("day_offset",): ("day_offset >= 1", "day_offset <= 5"),
        ("close",): ("close >",),
        ("source",): ("'fixture'", "'market_data'"),
        ("confidence",): ("confidence >=", "confidence <="),
    },
    "tb_review": {("max_drawdown",): ("max_drawdown <=",)},
    "pipeline_runs": {
        ("status",): ("'pending'", "'running'", "'completed'", "'failed'", "'timed_out'")
    },
    "pipeline_stage_attempts": {
        ("attempt_no",): ("attempt_no > 0",),
        ("status",): (
            "'pending'",
            "'running'",
            "'retrying'",
            "'completed'",
            "'failed'",
            "'timed_out'",
        ),
    },
    "order_submissions": {
        ("state",): ("'claimed'", "'submitted'", "'completed'", "'failed'"),
        ("stale_after", "claimed_at"): ("stale_after > claimed_at",),
    },
}
