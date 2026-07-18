# 설계서 → 구현 → 테스트 추적표

원본 계약은 [quantinue-integrated-design.html](../../docs/quantinue-integrated-design.html)이다. 이 문서는 계약을 복제하지 않고,
HTML 앵커와 현재 코드·검증 경로를 연결한다. 불일치가 있으면 HTML이 우선한다.

| 계약 / 요구 | 구현 경로 | 주요 검증 경로 |
|---|---|---|
| 사용자 요구 1, `#conv`: ontology와 공통 엔터티·이벤트·근거·판단·주문·리뷰 | `src/quantinue/core/ontology.py`, `core/schemas.py`, `core/contracts.py`, `api/schemas.py` | `tests/unit/test_ontology.py`, `test_schemas.py` |
| 사용자 요구 2, `#s01`~`#s11`: 역할 입력·출력·검증 | `src/quantinue/roles/role_01_*` … `role_11_*`의 `contracts.py`, `service.py` | `tests/unit/test_roles_01_04_contracts.py`, `test_roles_05_08_contracts.py`, `test_roles_09_10_contract_matrix.py`, `test_role_11_reviewer.py` |
| 사용자 요구 3: 역할별 시스템 프롬프트 | `src/quantinue/prompts/role_05_disclosure.md`, `role_06_news.md`, `role_07_strategist.md`, `role_08_critic.md`, `role_11_reviewer.md` | `tests/unit/test_llm_prompts.py` |
| 사용자 요구 4: 동일 인터페이스의 fixture/public 데이터, Mock/OpenAI/local LLM/Alpaca | `src/quantinue/market_data/`, `orchestration/factory.py`, `llm/provider.py`, `broker/contracts.py`, `broker/mock.py`, `broker/alpaca.py`, `broker/provider.py` | `tests/unit/test_market_data.py`, `test_market_data_public_pipeline.py`, `test_llm_provider.py`, `test_broker_provider.py` |
| 사용자 요구 5, `#relay`: 근거·출처·시각·신뢰도·실행 ID | `core/schemas.py`, `orchestration/pipeline.py`, `db/codec.py`, `db/postgres.py`, `db/domain_sources.py`, `db/reviews.py` — LLM provider/model/prompt/policy/input hash와 부모 근거를 원본·집계·T+5 저장까지 보존 | `tests/unit/test_pipeline_evidence_trace.py`, `tests/integration/test_persistence_postgres.py`, `test_provenance_postgres.py` |
| 사용자 요구 6: 실패·타임아웃·재시도·체크포인트·중복 주문·계좌/일 상한 | `orchestration/retry.py`, `checkpoint.py`, `lifecycle.py`, `db/order_reservations.py`, `broker/reservations.py`, `roles/role_09_risk_portfolio/`, `db/schema.sql` | `test_retry.py`, `test_checkpoint.py`, `test_pipeline_resilience.py`, `test_daily_order_cap.py`, `test_risk_order_contracts.py`, PostgreSQL 동시 예약·UNIQUE 검증 |
| 사용자 요구 7: DB 격리 | `compose.yaml` (`127.0.0.1:5444:5432`, 내부 `db:5432`), `db/schema.sql` | `scripts/test_compose_contract.sh`, `docker compose config` |
| 사용자 요구 8, `#security`: 비밀 없는 설정 안내 | `.env.example`, `.gitignore`, `.gitleaks.toml`, `scripts/scan_secrets.sh`, `src/quantinue/core/config.py` | `test_config.py`, `git check-ignore`, `scripts/scan_secrets.sh` |
| 사용자 요구 9: 설계 매핑 | 이 문서 | 경로 존재 여부 검토와 전체 테스트 |
| 사용자 요구 10: 단위·통합·Docker·반응형 | `tests/unit/`, `tests/integration/`, `tests/test_pipeline.py`, `tests/test_web.py`, `compose.yaml`, `src/quantinue/web/` | `uv run pytest`, Compose smoke, 브라우저 반응형 QA 증거 |
| 사용자 요구 11: 실제 키 직전 상태 | `docs/REAL_KEY_TESTING.md`, `.env.example`, 공급자 factory | 기본 skip + 문서의 opt-in 명령 |
| `#erd`: 정본 DDL과 실행 저장 | `db/schema.sql`, `src/quantinue/db/` | `tests/integration/test_schema_sql.py`, `test_persistence_postgres.py` |
| `#s01`, `#s02`, `#s04`~`#s06`: 키 없는 공개 데이터 경로 | `core/config.py`의 `DataMode`, `market_data/http_source.py`, `orchestration/factory.py`, 해당 역할 `service.py` | `tests/unit/test_market_data.py`, `test_market_data_public_pipeline.py` |
| `#relay`, `#erd`: 원천 공시·뉴스부터 signal/verdict/account/order/fill/review까지 정본 저장 | `db/domain_sources.py`, `domain_records.py`, `domain.py`, `postgres_lifecycle.py`, `reviews.py`, `orchestration/domain_lifecycle.py` | `tests/integration/test_domain_lifecycle_pipeline.py`, `test_domain_lifecycle_postgres.py`, `test_persistence_postgres.py` |
| `#s01`~`#s04`: 역할별 canonical stage 저장 | 01 완료 시 `tb_universe`, 02 완료 시 `tb_technical`, 03 완료 시 `tb_daily_pick`, 04 완료 시 `tb_macro`; `db/postgres_lifecycle.py`, `db/domain.py` | `tests/integration/test_domain_lifecycle_postgres.py`의 네 테이블 값 검증 |
| `#s07`, `#s08`: strategist signal과 독립 critic verdict | 08 완료 경계의 `db/postgres_lifecycle.py`는 07 결과를 `tb_strategist_signals`로 식별·저장하고 같은 ID에 `tb_critic_verdict`를 연결한다. 전략 판단 재합성이나 universe/daily-pick placeholder·backfill 없이 01·03 실제 부모 행을 사용 | `tests/integration/test_domain_lifecycle_pipeline.py`, `test_domain_lifecycle_postgres.py`의 ID 전달·signal/verdict 멱등성 및 실제 부모 값 검증 |
| `#s09`, `#s10`: no-trade, 원자적 계좌/일 상한, bracket leg 식별자 | `roles/role_09_risk_portfolio/`, `db/postgres_query.py`, `db/memory.py`, `broker/alpaca.py`, `db/domain.py` | `tests/unit/test_daily_order_cap.py`, `test_reviewer_no_trade.py`, `test_broker_provider.py`, `tests/integration/test_persistence_postgres.py` |
| `#s11`: 거래일 T+5, 명시적 POST trigger, 실행 화면 반영 | `roles/role_11_reviewer/calendar.py`, `processor.py`, `api/reviews.py`, `api/review_runtime.py`, `db/reviews.py`; `main.py`의 실행 상세와 `web/templates/dashboard.html`은 저장된 최종 review를 투영 | `tests/unit/test_role_11_reviewer.py`, `test_review_processor.py`, `test_reviewer_no_trade.py`, `tests/integration/test_domain_lifecycle_postgres.py`의 POST 후 run detail 검증, `tests/test_web.py` |
| 역할 주기: typed YAML 단일 cadence 정본과 due-role 명령 seam(daemon 제외) | `config/pipeline.yaml`의 `mvp.schedule`, `orchestration/policy.py`; cadence env override 없음, `phase_2`는 비런타임 메모 | `tests/unit/test_pipeline_policy.py` |
| 역할 판단·리스크·모델 설정 소유권 | `config/pipeline.yaml`의 `thresholds`, bracket ratios는 YAML 전용; `models`는 env로 명시한 adapter 모델명이 우선; 일 주문 상한은 `QUANTINUE_DAILY_NEW_ORDER_CAP`이 override. `orchestration/policy.py`, `factory.py`; 역할 07~09 | `tests/unit/test_pipeline_policy.py`, `test_roles_05_08_contracts.py`, `test_risk_order_contracts.py` |
| PostgreSQL 설정 키별 격리 | 앱은 `QUANTINUE_DATABASE_URL`(`127.0.0.1:5444` 또는 Compose `db:5432`), 통합·실키 테스트는 명시적 disposable `QUANTINUE_TEST_DATABASE_URL`; `localhost:5432` fallback 없음 | `scripts/test_compose_contract.sh`, `tests/integration/`, `tests/real_key/test_alpaca_real_key.py` |
| `#s01` | `roles/role_01_universe_screener/` | `test_roles_01_04_contracts.py` |
| `#s02` | `roles/role_02_technical_analysis/` | `test_roles_01_04_contracts.py` |
| `#s03` | `roles/role_03_daily_screener/` | `test_roles_01_04_contracts.py` |
| `#s04` | `roles/role_04_macro_analysis/` | `test_roles_01_04_contracts.py` |
| `#s05` | `roles/role_05_disclosure_analysis/` | `test_roles_05_08_contracts.py` |
| `#s06` | `roles/role_06_news_analysis/` | `test_roles_05_08_contracts.py` |
| `#s07` | `roles/role_07_strategist/` | `test_roles_05_08_contracts.py` |
| `#s08` | `roles/role_08_critic/` | `test_roles_05_08_contracts.py` |
| `#s09` | `roles/role_09_risk_portfolio/` | `test_roles_09_10_contract_matrix.py`, `test_risk_order_contracts.py` |
| `#s10` | `roles/role_10_order_execution/`, `broker/` | `test_roles_09_10_contract_matrix.py`, `test_broker_provider.py` |
| `#s11` | `roles/role_11_reviewer/` | `test_role_11_reviewer.py`, `test_role_11_parameter_matrix.py` |
| `#security`: ticker·paper-only·자격증명·주문 경계 | `api/schemas.py`, `core/schemas.py`, `core/config.py`, `broker/alpaca.py`, `db/schema.sql`, `.env.example` | `test_api_ticker_boundary.py`, `test_config.py`, `test_broker_provider.py`, PostgreSQL 카탈로그 테스트 |

## 운영 검증 명령

```bash
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run pytest
sh scripts/test_compose_contract.sh
docker compose config
./scripts/scan_secrets.sh
```

Docker 실기동과 반응형 브라우저 검증은 격리된 임시 Compose 프로젝트에서 수행하며,
프로젝트 이름·포트·정리 영수증을 `.omo/evidence/`에 남긴다.

## 의도적으로 남은 운영 경계

- 애플리케이션 내부 background daemon, timer, worker는 없다. 외부 호출자가 due-role seam과
  T+5 endpoint를 호출한다.
- 기본 테스트는 실제 공개 인터넷, OpenAI 또는 Alpaca 자격증명을 사용하지 않는다.
  실제 키 검증은 [REAL_KEY_TESTING.md](REAL_KEY_TESTING.md)의 명시적 opt-in 절차만 사용한다.
- Alpaca는 paper endpoint만 허용한다. live trading endpoint는 설정 검증에서 거부한다.
- Compose는 호스트 `127.0.0.1:5444`만 Quantinue DB에 게시하고 컨테이너 내부에서는
  `db:5432`를 사용한다. 호스트 `5432`를 게시하거나 기존 컨테이너를 조작하지 않는다.
