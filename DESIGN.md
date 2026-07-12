# Quantinue 통합 설계서 Design System

## 1. Atmosphere & Identity

신뢰할 수 있는 기술 정본이자 팀의 작업대다. 차분한 GitHub 계열 중립색 위에 파란색을 탐색과 정보, 초록·노랑·빨강을 상태에만 사용한다. 시그니처는 “긴 설계를 빠르게 찾는 좌측 목차 + 근거를 보존하는 결정 카드”다. 이번 개선은 외형을 교체하지 않고 현재 계약과 기록의 위계를 명확히 한다.

## 2. Color

| 역할 | 토큰 | 값 | 용도 |
|---|---|---:|---|
| 배경 | `--bg` | `#f6f8fa` | 페이지 배경 |
| 카드 | `--card` | `#ffffff` | 섹션·표·카드 |
| 본문 | `--ink` | `#1f2328` | 제목·본문 |
| 보조 | `--sub` | `#57606a` | 메타·설명 |
| 경계 | `--line` | `#d0d7de` | 표·카드 구분 |
| 탐색·정보 | `--blue` | `#0969da` | 링크·포커스·정보 |
| 완료 | `--green` | `#1a7f37` | 해결·완료 |
| 협의 | `--amber` | `#9a6700` | 보류·협의 |
| 차단 | `--red` | `#cf222e` | 충돌·블로킹 |
| AI | `--purple` | `#8250df` | AI 검토 표식 |

규칙: 색만으로 상태를 전달하지 않는다. 상태는 항상 텍스트와 함께 표시한다. 신규는 정보색, 완료는 초록, 협의는 노랑, 차단은 빨강을 사용한다.

## 3. Typography

| 단계 | 크기 | 굵기 | 행간 | 용도 |
|---|---:|---:|---:|---|
| H1 | 30px | 700 | 1.25 | 문서 제목 |
| H2 | 22px | 700 | 1.35 | 주요 섹션 |
| H3 | 17px | 700 | 1.45 | 하위 섹션 |
| Body | 15px | 400 | 1.65 | 본문 |
| Table | 13.5px | 400 | 1.55 이상 | 표 |
| Metadata | 12.5–13px | 500–700 | 1.45 | 배지·메타 |

- 본문: `Pretendard`, `Apple SD Gothic Neo`, `Malgun Gothic`, system-ui, sans-serif.
- 코드: `Consolas`, `D2Coding`, monospace.
- 한국어 산문은 `word-break: keep-all`; 코드·URL·식별자는 필요할 때만 줄바꿈한다.
- 모바일 표와 카드 본문은 14px 미만으로 낮추지 않는다.

## 4. Spacing & Layout

- 기준 단위: 4px.
- 본문 최대 폭: 1080px.
- 데스크톱 1280px 이상: 260px 고정 목차 + 최소 24px 간격 + 본문.
- 721–1279px: 문서 흐름 안 목차 + 상단 sticky 목차 바로가기.
- 720px 이하: 14px 페이지 여백, 세로형 AI 카드, 핵심 표의 카드형 전환.
- 긴 표의 가로 스크롤은 페이지 전체 폭을 늘리지 않는 독립 영역에서만 허용한다.

## 5. Components

### Section card
- 구조: 제목, 설명, 표·다이어그램·콜아웃.
- 상태: 기본, `:target` 강조, 키보드 포커스.
- 접근성: `main` 안에 배치하고 제목과 섹션을 연결한다.

### Table wrapper
- 구조: 스크롤 안내 + 표.
- 변형: matrix-scroll, mobile-cards.
- 상태: 기본, overflow, focus-visible.
- 접근성: caption, column scope, overflow일 때 focus와 aria-label 제공.

### Decision card
- 구조: 헤더, 현재 결론, 이전 논의.
- 변형: open, deferred, blocking, done/archive.
- 완료 카드는 결론을 먼저 노출하고 이전 논의는 `<details>`로 접는다.

### Current status board
- 구조: 착수 판단, 블로커, 1차 목표, 다음 행동.
- 상태: 조건부 GO, blocked, ready.
- 접근성: 제목과 상태가 텍스트로 완결되어야 한다.

### Table of contents
- 데스크톱: 좌측 고정.
- 모바일·태블릿: 상단 sticky 바로가기, 문서 내 목차로 이동.
- 포커스가 보이고 목적지 제목으로 이어져야 한다.

## 6. Motion & Interaction

- 동작은 앵커 이동, 세부 내용 펼침, 상태 필터에만 사용한다.
- 전환은 150–200ms의 `opacity`·`transform`만 허용한다.
- `prefers-reduced-motion`에서는 부드러운 스크롤을 끈다.
- 장식 목적의 애니메이션은 사용하지 않는다.

## 7. Depth & Surface

혼합 전략을 유지한다. 일반 섹션은 1px 경계, 고정 목차와 sticky 도구막대만 낮은 그림자를 사용한다. 상태 카드는 배경 톤과 경계색으로 구분한다. 그림자의 광원은 위쪽으로 통일하고, 본문 카드에는 추가 그림자를 늘리지 않는다.
