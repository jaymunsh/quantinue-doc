# MVP1 해설형 01–11 UI 런타임 감사

검증일: 2026-07-17

## 가설과 실제 판정

1. **해설이 fixture 실행을 실제 SEC·Google 네트워크 수집으로 오해하게 만든다 — 최초 확인 후 수정.** `data_mode`에 따라 public은 SEC EDGAR/Google News RSS, fixture는 네트워크를 호출하지 않는 내장 시연 데이터로 분기했다. 실제 fixture 런타임 DOM에서 해당 안내 2개를 확인했다.
2. **색만 바뀌고 01–11의 순서가 한눈에 이어지지 않는다 — 기각.** 데스크톱은 A→B→C→D 4열 화살표, 태블릿·모바일은 연속 하향 화살표로 렌더링한다. 세 뷰포트 모두 walkthrough 4개와 역할 11개를 확인했다.
3. **11의 completed 상태가 실제 T+5 평가 완료처럼 보이거나, 반대로 미구현처럼 숨겨질 수 있다 — 확인 후 수정.** 상세 원장에서는 제목을 `리뷰 예약`, 상태를 `등록 완료`로 표시하고 실제 요약·facts·items를 그대로 보여준다. 이번 실행은 리뷰 대상을 등록하며, PostgreSQL 모드에서 운영자가 처리 API를 호출할 때 T+1~T+5 가격과 최종 성과를 멱등 처리한다. 내장 스케줄러나 daemon은 없다.
4. **전략가 해설이 실제 계산보다 넓은 근거를 주장한다 — 최초 확인 후 수정.** 기술·공시·뉴스·모델 점수 평균과 일일 후보·최소 확신도 게이트만 설명하도록 실제 Role07 구현과 맞췄다.
5. **상세 설명 때문에 모바일이 가로로 넘치거나 한국어가 깨진다 — 기각.** Chromium 1440×1000, 768×900, 390×844에서 document overflow 0, console error 0이며 독립 CJK 검토가 PASS했다.

## 관측 결과

- HTTP POST `/api/runs`: 201, fixture NVDA 11단계 완료
- 해설 흐름 카드: 4
- 역할 상세 블록: 11
- 05–10 방법 해설 패널: 6
- 리뷰 예약/등록 완료: 각 1
- 모드 중립 01/05/06 설명: 각 1

## 증거

- `.omo/evidence/mvp1-explainer-desktop.png`
- `.omo/evidence/mvp1-explainer-tablet.png`
- `.omo/evidence/mvp1-explainer-mobile.png`

## 정리

시각 QA용 `127.0.0.1:8099`는 검증 후 종료한다. `.env`, 비밀값, 제품 Docker와 호스트 5432 자원은 접근하거나 변경하지 않았다. 기존 dirty/untracked 파일은 보존했다.
