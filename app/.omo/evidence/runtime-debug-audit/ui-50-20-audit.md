# 50→20→10 상세 화면 런타임 감사

- 실행 ID: `05e130ae3b6842e7b141180e2b7f7d84`
- 안전 모드: public data, memory database, local LLM, mock broker, trading disabled
- 결과: 01–11 completed

## 가설 판별

1. 실패 종목 보충 후에도 기술 분석 결과가 20개보다 적게 완료될 수 있다.
   - 판별: 이전 조건은 10–19개도 허용해 확인됨.
   - 수정: 같은 50개 유니버스에서 보충한 뒤 성공 결과가 정확히 20개가 아니면 실패.
   - 증거: 실제 실행 `50 / 20 / 10`, 19개 성공 경계 회귀 테스트 PASS.
2. 공시·뉴스 공급자가 여러 건을 반환해도 화면에는 대표 한 건만 남는다.
   - 판별: 기존 구현에서 확인됨.
   - 수정: 대표 분석 1건과 전체 수집 목록을 구분해 보존·표시.
   - 증거: 실제 실행 공시 1,003건, 뉴스 25건; 대표 분석 표기와 URL 정제 확인.
3. mock 브로커 화면이 외부 주문 또는 실제 체결로 오해될 수 있다.
   - 판별: 기존 요약 표현은 모드 구분이 약했음.
   - 수정: mock/trading-disabled는 로컬 모의 처리, Alpaca Paper는 Paper 주문 처리로 조건부 표시.
   - 증거: health broker mode `mock`, 주문 없음, `로컬 모의 처리 · 주문 생략, 0주`.

## 화면 및 정적 검증

- Chromium desktop/mobile: 2/2 PASS
- 역할 11개, 단계 구분선 4개, 의사결정 카드 3개
- 문서 가로 넘침 0, 콘솔 오류 0
- Ruff format/check PASS, basedpyright 0 errors, pytest 475 passed / 22 skipped

## Cleanup

- 최신 안전 서버는 요청에 따라 `127.0.0.1:8001`에서 유지.
- Docker 및 PostgreSQL 5432는 사용하거나 검사하지 않음.
- 환경 파일과 비밀값은 읽거나 기록하지 않음.

## 최종 PostgreSQL 저장 경계 감사

- 가설 1: UI 전용 `is_requested_focus`가 canonical PostgreSQL INSERT로 누출된다.
  - 실제 disposable PostgreSQL에서 `Unconsumed column names: is_requested_focus`로 확인.
  - 역할 03 선정 항목과 역할 02 기술 스냅샷 저장 projection에서 해당 UI 전용 필드만 제외.
- 가설 2: disposable schema가 애플리케이션 metadata보다 오래됐다.
  - 기각. runner는 현재 `db/schema.sql`로 매회 초기화했고 canonical 테이블에 UI 표시 필드가 없는 것이 의도된 계약.
- 가설 3: 50→20 cardinality가 PostgreSQL 제약을 위반한다.
  - 기각. 최초 실패는 DB 실행 전 SQL 컴파일 단계였고, projection 수정 후 동일 실제 시나리오가 통과.
- red→green: 동일 disposable 통합 시나리오가 수정 전 CompileError, 수정 후 `25 passed in 12.47s`.
- 최종 게이트: Compose contract PASS, Ruff format/check PASS, basedpyright 0 errors, pytest 475 passed/22 skipped.
- cleanup: runner 자체 정리 완료. localhost:5432, 제품 DB/Compose, `.env`, 비밀값은 검사·사용·변경하지 않음.
