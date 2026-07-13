# quantinue-live-progress - Work Plan

## TL;DR

**What you'll get:** 실행 버튼을 누르면 바로 운영실로 돌아오고, 현재 실행 중인 역할(예: 공시 수집·뉴스 수집·strategist·critic)과 완료·대기 단계를 1.5초마다 확인할 수 있는 진행 패널.

**Why this approach:** 기존 checkpoint/attempt를 새 상태 저장소로 복제하지 않고, 실행 중 claim context와 attempt를 안전한 읽기 전용 상태로 투영한다. 서버 렌더링은 기본으로 유지하고, 실행 중일 때만 작은 same-origin 폴링으로 화면을 갱신한다.

**What it will NOT do:** 스케줄러, 실거래, WebSocket/SSE, 새 데이터 공급자, raw 오류·프롬프트·비밀값 표시는 추가하지 않는다.

## Scope

### Must have
- Form/API가 실행을 백그라운드로 시작하고, 중복 요청은 기존 idempotent run에 합류한다.
- 실행 중 context/attempt를 memory 및 disposable PostgreSQL 경계에서 안전하게 조회하고 `ControlRoomRun`으로 투영한다.
- 대시보드는 현재 역할, 완료 단계, 다음 대기 역할, 재시도/실패 코드, 경과 상태를 표시한다.
- 실행 중일 때만 same-origin 1.5초 폴링; terminal 상태에서는 중단한다. ARIA live 상태를 제공한다.
- 테스트, real Chromium 390/768/1024/1440, disposable PostgreSQL 및 cleanup 증빙.

### Must not have
- localhost:5432 검사·사용·중지·변경, 제품 Docker 실행, `.env`/비밀값 변경, scheduler/polling job, unsafe content 노출.

## Todos

- [x] 1. Expose active pipeline snapshots from the run-store boundary.
  - Capture running/retrying `PipelineContext` plus attempts without raw exception detail, preserving terminal behavior and idempotency.
  - TDD: memory and disposable PostgreSQL active/terminal snapshot behavior; legacy defaults remain safe.

- [x] 2. Start runs asynchronously and project active state through the safe control-room API.
  - Keep background task references/lifespan cleanup; form returns 303 immediately; API adds a backward-compatible asynchronous start surface or explicit response model.
  - TDD: immediate response, duplicate request behavior, active stage/next-stage projection, terminal transition.

- [x] 3. Render and poll the live pipeline panel.
  - Add a small same-origin script only while a nonterminal run exists; update only safe status DOM, stop at terminal state, preserve no-JS baseline and reduced-motion/focus/live-region design contract.
  - TDD: rendered states and browser interaction at mobile/desktop.

- [x] 4. Verify lifecycle, runtime, and visual behavior.
  - Run static/full tests, disposable PostgreSQL, Compose contract, and fresh Chromium at 1440/1024/768/390 for slow-role running, retrying, terminal, and no-JS fallback states.

## Final verification wave

- [x] F1. Goal/constraint review
- [x] F2. Code-quality review
- [x] F3. Hands-on lifecycle QA
- [x] F4. Security/context review and three-hypothesis runtime audit

## Success criteria

- A local-LLM run visibly advances from collection through strategist and critic without waiting for terminal completion.
- The latest safe control-room snapshot updates in place, stops polling when terminal, and never displays raw model/provider/error content.
- Memory mode works for one local server lifetime; PostgreSQL verification uses only the disposable runner.
