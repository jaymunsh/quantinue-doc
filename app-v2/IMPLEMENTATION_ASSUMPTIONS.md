# Quantinue 구현 보완사항

이 문서는 [원본 계약](../docs/quantinue-integrated-design.html)을 바꾸지 않고, 바로 실행 가능한
1차 구현을 위해 추가로 판단한 내용을 기록한다. 설계서와 충돌하면 설계서가 우선한다.

## 이번 구현에서 확정한 것

- 스케줄 구현은 due 역할만 계산하는 순수 명령 seam이며 MVP 내부에서 백그라운드 daemon,
  timer 또는 worker process를 시작하지 않는다.

- 실행 주체: 현재는 1인 개발, 이후 각 담당자가 `roles/role_01_*`부터 `role_11_*`까지 독립 교체한다.
- 1차 성공 조건: NVDA 한 종목, 페이퍼 계좌 한 개, 정상 전량 체결 한 건이 01부터 11까지 중복 없이 완주한다.
- 기본 실행 모드: LLM `mock`, broker `mock`, database `memory`. 계정이나 API 키 없이도 같은 결과가 재현된다.
- 데이터 모드: `fixture`는 네트워크 없는 결정론적 기본값이고, `public`은 별도 키 없이
  01 NASDAQ 스크리너, 02 공개 일봉, 04 FRED CSV, 05 SEC submissions, 06 RSS를 사용한다.
  공개 원문 기사 크롤링은 하지 않는다.
- 실제 연동 방식: `.env`에서 어댑터 모드를 바꾼다. 역할 서비스는 외부 공급자를 직접 참조하지 않는다.
- 화면: 수익률 대시보드가 아니라 파이프라인 운영실이다. 역할별 완료 상태, 판단, 주문, 회고 연결을 우선한다.
- 데이터 보존: 15개 도메인 테이블과 4개 운영 테이블의 정본은 `db/schema.sql`이다. `pipeline_runs`는 실행 추적용이며 도메인 식별자의 부모가 아니다.
- PostgreSQL 파이프라인은 원천 `tb_disclosure`·`tb_news`, 분석 signal, strategist signal,
  critic verdict, paper account, 예약·조정된 order, fill, review snapshot과 최종 review까지
  정본 계보를 저장한다.
- 01 `tb_universe`, 02 `tb_technical`, 03 `tb_daily_pick`, 04 `tb_macro`는 각 역할의 검증된
  출력이 완료되는 즉시 자체 stage 경계에서 저장한다. 08 경계는 07 strategist 결과를 새로
  합성하는 곳이 아니다. 07 결과의 canonical strategist signal ID와 paper account ID를 만든
  뒤, 그 signal에 08 critic verdict를 연결해 09·10이 같은 ID를 사용하게 한다. 이때 08은
  universe·daily-pick placeholder를 생성하거나 누락 부모를 backfill하지 않고 01·03의 실제
  정본 행만 참조한다.

## 안전을 위해 보수적으로 정한 것

- Alpaca paper 어댑터는 구현되어 있다. 기본값은 `BROKER_MODE=mock`이며 paper 주문은 `TRADING_ENABLED=true`와 별도의 `CONTROL_ROOM_TOKEN`을 동시에 설정해야 한다. live endpoint는 설정 검증에서 거부한다.
- 1차 주문은 정수 수량, 정규장, 계좌 equity 대비 리스크 예산·포지션 상한, 고정 손절 15%,
  익절 20% 브래킷을 사용한다. 현재 파이프라인 계좌 스냅샷은 결정론적 paper 값이며 실제
  broker 잔고 동기화는 운영 보완 범위다.
- LLM 출력은 Pydantic 스키마를 통과한 `score`, `label`, `reason`만 내부로 들어온다.
- 뉴스 입력은 제목과 RSS 스니펫뿐이다. 원문 크롤링은 하지 않는다.
- 같은 `ticker + cycle_ts` 요청은 기존 실행을 반환한다.
- 근거 신선도 기본값은 5분이다. 판단 시각보다 미래인 근거 또는 5분을 넘긴 근거는 사용하지 않는다.
- 근거가 약하거나 필수 근거가 누락되면 추론으로 보충하지 않고 `hold`한다.
- 고점 추격(late-entry) 판정은 03 일일 스크리너에서만 수행하고 뒤 역할에서 중복 판정하지 않는다.
- Critic 결과는 `UNIQUE(signal_id)`로 한 signal당 최대 한 행만 허용한다.
- 리뷰 가격 출처의 닫힌 집합은 `fixture | market_data`, 주문 상태는 `planned | submitted | filled | failed | canceled`이다.
- 리스크 단계는 저장소의 원자적 예약으로 계좌별 거래일 신규 주문 상한을 적용한다.
  동일 signal 재실행은 같은 idempotency key를 사용하며 broker 예약 계층도 중복 제출을 막는다.
- 주문이 생성되지 않은 판단은 `no_trade`로 리뷰 계보를 종료한다.
- Alpaca bracket 응답은 parent order ID뿐 아니라 stop/take-profit leg ID를 보존한다.
- T+5는 미국 주식 거래일과 정규장 종가 기준이다. 주입 가능한 clock을 쓰는 processor를
  `POST /api/reviews/{signal_id}/process`로 명시적으로 호출하며 T+1~T+5 스냅샷과 최종 결과를
  멱등 upsert한다. 확정 결과는 별도 표시용 복제본이 아니라 실행 조회 시 저장된 review를
  다시 투영하므로 `GET /api/runs/{run_id}`와 새로고침한 운영실에 함께 반영된다.

## 담당자와 함께 보완할 내용

1. Alembic 마이그레이션 도입 여부 결정 (`db/schema.sql` 정본은 완료)
2. 공개 공급자의 운영 SLA, 증분 수집, 캐시와 공급자 교체 정책
3. 09의 실제 paper 계좌 잔고 동기화 및 다계좌 수량 정책
4. 10의 부분 체결·취소 이후 broker 상태 재조정
5. due 역할 및 T+5 endpoint를 호출할 외부 scheduler/worker 운영 방식

## 2차를 막지 않기 위한 경계

- LLM과 broker는 `Protocol` 뒤에 있다. OpenAI, 로컬 모델, Alpaca를 역할 코드 수정 없이 교체한다.
- 실행 저장소는 memory와 PostgreSQL 구현이 같은 계약을 따른다.
- 역할은 불변 `PipelineContext`를 받아 새 컨텍스트를 반환한다.
- 실행·단계 체크포인트, 프롬프트/모델 버전, 입력 해시와 실패 상태는 1차 운영 안정성을 위해 먼저 구현했다. 다계좌 주문은 2차 범위다.

## 로컬 격리와 외부 연동 전제

- `QUANTINUE_DATABASE_URL`은 애플리케이션 저장소 전용 키다. 호스트에서 전용 Compose DB에
  접근할 때만 `127.0.0.1:5444`, Compose 앱 컨테이너에서는 `db:5432`를 사용한다.
- `QUANTINUE_TEST_DATABASE_URL`은 PostgreSQL 통합·실키 테스트가 명시적으로 제공받는 폐기
  가능한 테스트 DB 전용 키다. 테스트는 이 키가 없으면 skip하며 애플리케이션 URL이나
  `localhost:5432`를 대체 기본값으로 추측하지 않는다.
- Compose는 `db/schema.sql`을 새 전용 볼륨의 init 스크립트로 마운트한다. 기존 볼륨에는 PostgreSQL 특성상 재적용하지 않는다.
- OpenAI 호출은 비용이 발생할 수 있고 Alpaca paper 호출은 paper 주문 상태를 바꿀 수 있다. 둘 다 기본 테스트에서 제외하고 명시적 opt-in만 허용한다.
- 기본 검증은 공개 공급자도 실제 인터넷에 호출하지 않는다. 실제 네트워크, OpenAI 키,
  Alpaca paper 키를 이용한 실행은 사용자가 명시적으로 opt-in해야 한다.
- `.env.example`의 값은 예시뿐이다. 실제 비밀값은 추적되지 않는 `.env` 또는 외부 secret manager에만 둔다.

## 런타임 설정 소유권

- `config/pipeline.yaml`의 `mvp.schedule`만 04·05·06·07 런타임 cadence를 소유한다. 환경변수나
  다른 코드 기본값은 운영 cadence의 두 번째 정본이 아니다. `phase_2` 값은 현재 런타임에
  읽히지 않는 향후 범위 메모다.
- `mvp.thresholds`, `stop_loss_ratio`, `take_profit_ratio`는 역할 07~09 런타임 정책의 YAML
  정본이며 환경변수 override를 제공하지 않는다.
- `mvp.models`는 mock/OpenAI/local adapter 모델명의 기본값이다. 사용자가 해당
  `QUANTINUE_*_MODEL` 환경변수를 명시하면 그 값이 우선한다.
- `QUANTINUE_DAILY_NEW_ORDER_CAP`은 저장소가 원자적으로 적용하는 운영 상한의 명시적 env
  override다. 데이터·LLM·broker·database adapter 선택도 `QUANTINUE_*_MODE`가 소유한다.
