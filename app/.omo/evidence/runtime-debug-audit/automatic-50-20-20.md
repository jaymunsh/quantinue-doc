# 자동 50 → 20 → 20 런타임 디버그 감사

검증일: 2026-07-17

## 가설과 판정

1. **03 역할이 여전히 10개로 자른다 — 기각.** 실제 일봉 경계의 배치 런타임에서 기술 분석 20개와 서로 다른 최종 후보 20개를 확인했다. 자동 실행에서는 숨은 기본 티커를 강제로 포함하지 않는다.
2. **티커 없는 기본 폼이 예전 단일 종목 실행으로 간다 — 기각.** `POST /runs`는 자동 screening entry point를 예약하고, 후보 실행에는 `automatic=true`와 영속 순위가 기록된다.
3. **후보별 공시가 NVIDIA CIK를 재사용한다 — 기각.** SEC 공식 company ticker index에서 정규화한 ticker별 10자리 CIK를 해석하며, 인덱스는 앱 수명 동안 한 번만 가져온다.
4. **공통 discovery seed가 PostgreSQL 재시작 경계에서 깨진다 — 확인 후 수정.** 최초 구현의 checkpoint insert에 필수 hash가 빠져 실제 disposable PostgreSQL에서 실패했다. hash 저장과 후보별 01/03/04 정본 선행 저장을 적용했고 통합 테스트로 strategist signal까지 통과함을 확인했다.
5. **20개 상세 카드를 펼치면 반응형 레이아웃이 넘친다 — 기각.** 실제 Chromium 1440×1000 및 390×844에서 카드 20개, 첫 카드 역할 7개(05~11), document overflow 0, console error 0을 확인했다.

## 증거

- `.omo/evidence/automatic-20-desktop-expanded-final.png`
- `.omo/evidence/automatic-20-mobile-expanded-final.png`
- 단위/웹 전체: 529 passed, 27 expected skips
- disposable PostgreSQL: 31 passed
- Compose contract: PASS

## 정리

시각 QA용 127.0.0.1:8099 서버와 임시 스크립트를 제거했다. disposable PostgreSQL runner가 소유한 자원은 runner cleanup으로 정리되었다. 제품 Docker, `.env`, 비밀값, 호스트 5432 자원은 접근하거나 변경하지 않았다.
