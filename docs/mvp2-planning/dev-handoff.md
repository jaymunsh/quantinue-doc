# 개발 착수 핸드오프 (다음 세션용)

> 작성 2026-07-18 · usage 절약으로 복사·메모까지만 하고 중단. 다음 세션이 여기서 이어감.

## 지금까지 완료
- 문서 v4.4 완성 (`docs/quantinue-integrated-design.html`) · GitHub Pages 서빙 · 이미지 `docs/assets/`로 이동(경로 깨짐 해결)
- 계획 정본: `phase0-asbuilt-audit.md`(감사) · `phase1-decisions.md`(결정 R1~R10) · `phase2-dev-plan.md`(마스터 계획 M1~M11)
- **`app-v2/` 생성 완료** — 깨끗한 1차 커밋 `6163630`에서 소스만 복사(23MB·453파일, 잡동사니 제거). `.env`는 현재 것 복사(MLX 8888·Alpaca 페이퍼 키·정리 완료). **아직 git 미커밋**(로컬만).
  - 재생성 필요 시: `rm -rf app-v2 && mkdir app-v2 && git archive 6163630 app | tar -x --strip-components=1 -C app-v2 && cp app/.env app-v2/.env` + 잡동사니 제거

## 다음 할 일 (순서)
1. **app-v2 git 커밋** — 2차 dev baseline으로 1커밋 (사용자 확인 후). node_modules/.pyc는 .gitignore됨.
2. **Wave 0 무장·드라이런** (월요일 개장 전까지, 급하지 않음 — 토요일이라 체결은 월 20일):
   - `app-v2/.env`: `QUANTINUE_DATA_MODE=public` 확인 · `QUANTINUE_LLM_MODE=local`(MLX 8888) · **`QUANTINUE_BROKER_MODE=alpaca` + `QUANTINUE_TRADING_ENABLED=true`로 변경**(현재 mock/false — 실 페이퍼 주문 스위치, 켜기 전 확인)
   - Postgres 기동 → schema 적용 → NVDA 1종목 드라이런(mock 브로커로 먼저) → OK면 실 페이퍼 무장 → 월요일 09:30 뉴욕 개장 대기
   - 목표: 20일 매수 → T+5 = 27일(마감일) 안에 첫 회고 데이터 확보
3. **마일스톤 착수** (Wave 1~4, `phase2-dev-plan.md` 순서): M1 스케줄러·멱등 → M2 스키마(reason JSONB·신규 3테이블·07/08 계보·side sell) → M3 깔때기 → M4 방어선 → M5 매도 → M6 계좌·서킷 → M7 학습 → M8 운영 → M9~11 서비스·배포. 각 마일스톤은 writing-plans로 TDD 상세 플랜 생성 후 진행.

## 주의
- 문서 미러 규칙: 핵심 로직·프롬프트(페르소나)는 **코드 확정 후** 문서 `#logic`에 반영(추측 선기재 금지)
- 문턱값·주기·한도는 전부 config 소유 · ⚙️ 조정 가능
- app/(원본 1차)은 다른 작업자 WIP 있음 — 2차 개발은 app-v2/에서만
