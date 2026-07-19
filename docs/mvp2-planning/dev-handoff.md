# 개발 핸드오프 — 현재 상태

> 최종 갱신 2026-07-19. **이 파일은 "지금 어디까지 왔나"만 담는다.**
> 실행 지시는 전부 **`dev-playbook.md`**에 있다 — 새 세션은 그 파일을 열고 이어가면 된다.

## ⭐ 먼저 읽을 것

1. **`docs/mvp2-planning/dev-playbook.md`** — 실행 정본. 마일스톤별 완료 표시(✅/🔶)와 남은 태스크, ⏳ 보완 목록이 전부 여기 있다.
2. `docs/quantinue-integrated-design.html` — 설계 정본(v5.2). 확정 로직은 `#logic`, 결정 이력은 changelog, 결함·교훈은 `#troubleshooting`.
   - **스키마 표 색 규칙**: 초록 배경 = 2차 **신규** 컬럼·테이블 · 노랑 배경 = 2차 **제약 변경**(기존 컬럼). 범례는 "테이블별 컬럼 명세" 바로 아래.
   - ⚠️ **스키마를 바꾸면 반드시 여기까지 반영**: `db/schema.sql` · `db/migrations/mvp2.sql` · `tests/integration/schema_sql_expectations.py` · 정본 HTML(컬럼 표 + ERD + 데이터 사전 + changelog).
3. `docs/mvp2-planning/ghost-config-audit.md` — **유령 설정·컬럼 감사**(2026-07-19). 선언만 되고 소비자가 없는 값 전수 조사. ⚠️ **M5·M6·M8 착수 전 반드시 볼 것** — 성향별 리스크 한도와 LLM 예산 상한이 현재 하나도 적용되지 않는다.
4. `docs/mvp2-planning/troubleshooting-log.md` — **트러블슈팅 기록**. 증상→원인→조치→교훈. **정본 HTML 트러블슈팅 섹션의 원본** — 정본 갱신 시 여기서 옮긴다.
5. `docs/mvp2-planning/m4-scope-decisions.md` — **M4 범위 결정 기록**(2026-07-19). 코드 실사에서 나온 사실 F1~F8과 그에 따른 범위·순서 재정의(D1~D6). 각 결정에 근거와 "뒤집을 조건"을 병기 — 나중에 수정할 때 여기부터 볼 것.

## 현재 상태 (2026-07-19)

| 항목 | 상태 |
|---|---|
| 작업 브랜치 | **`sunghyuk`** (여기서 계속 작업) |
| main 병합 | **Wave 0~1 병합 완료**(커밋 `818416e`, `--no-ff`). **push는 안 함** — 공유 저장소이고 `app/`에 다른 작업자 WIP가 있어 사용자 확인 후 진행 |
| 테스트 | 유닛/웹 **681 green** · 통합 **63 green** · ruff clean |
| DB | app-v2 전용 **포트 5445**(`app-v2-db-1`), M2 마이그레이션 적용 완료. 1차 `app-db-1`(5444)은 **다른 작업자 WIP — 불간섭** |
| 앱 실행 포트 | **8020** (8000은 다른 프로세스 점유) |

### 완료
- **W0** 드라이런까지 — 01→11 완주 검증 (실 페이퍼 무장 W0-7·W0-8만 남음)
- **M1** 슬롯 멱등·NYSE 캘린더·자동 스케줄러(config `mvp2.schedule.enabled=false`로 꺼둠)
- **M2** 스키마·계약 일괄 확장 + 무손실 멱등 마이그레이션
- **M3** 깔때기 복원 (2000 → 500 → 50 → 20)
- **M4 검증 라운드** ✅ (2026-07-19) — 방어선 E2E 강제 발동 5종 · **halted 생략이 런을 죽이던 버그 수정** · 크리틱 문턱 이중값 정리(0.60→0.70) · `tb_order_plan` 신설로 방어선 발동 관측 가능화
- **M6** 🔶 **거의 완료** — 6-1 ✅(계좌별 계획·집행. **실 브로커는 팬아웃 안 함** — 외부 계좌가 하나라 7배 주문이 된다) · 6-2 🔶 3/4(`daily_loss_limit`만 M5 이후) · 6-3 ✅ · 6-4 ✅. 잔여: role_11 계좌별 채점(M7)
- **M4** ✅ **완료 (2026-07-19)** — 방어선 8건 + 신설 2건(4-0 role_05 CIK 실배선 · 4-9 role_09 배선). 범위 결정 근거는 `m4-scope-decisions.md`

### 다음 할 일
1. **월요일 개장 시(KST 22:30)**: playbook **W0-7**(실 페이퍼 무장 — ⚠️ 사용자 확인 필수) → **W0-8**(스모크·첫 체결) → T+5 시계 가동
2. ✅ **드라이런 완료** (AAPL, HTTP 201, 01→11 완주) — 실행에서만 드러나는 결함 2건 수정(SEC UA 403 · NASDAQ과 UA 충돌). **단 갭 가드·late_entry·halted는 크리틱 선차단으로 미발동 — 유닛 검증만 된 상태**. block 매체도 표본에 차단 도메인이 없어 미실측. 상세: `m4-scope-decisions.md` §드라이런 실측
3. ℹ️ **주문 규모**: M6-2에서 **자본이 계좌에서 오게 되어** env 우회는 사실상 무력해졌다(계좌가 있으면 그 자본을 쓰고 현금 바닥은 `min_cash_ratio`가 지킨다). `.env` 기록은 아래 유지.
4. ✅ **주문 규모 조정 (2026-07-19)** — `.env`의 `MAX_APP_ORDER_EXPOSURE_USD`를 `1000000.00` → **`90000.00`**. 그 전 설정이면 첫 주문이 종목당 **$250,000** 규모로 나갔다(이 값이 사이징의 equity로 이중 사용됨). 현재: 포지션당 $22,500 · 4건에서 천장 · 현금 10% 유지. **M6-2에서 `min_cash_ratio` 배선 시 되돌릴 것.** 백업 `.env.bak-m4`
5. ⚠️ **배포 전**: `QUANTINUE_HTTP_USER_AGENT`를 실제 연락 가능한 주소로 설정(SEC 공정접근 — 기본값은 동작하나 응답 불가)
5. 이후 **M5 매도**(최대 설계 작업 — 착수 시 첫 태스크는 매도 주문 표현 설계) → M6 계좌·서킷

## 확정된 정책 (되묻지 말 것)

- **거래 세션**: 최종 목표는 **전 세션 개방**. 활성화는 전제 충족 순 — W0 정규장 → M5 장외 **청산** → M5+ 보호주문 상태기계 완성 후 장외 **매수**. 매수만 조건부인 이유는 장외에서 브래킷이 불가해 손절 공백이 생기기 때문. (상세: playbook ⏳목록 "정책(결정됨)" 행)
- **작업 위치**: `app-v2/` 전용. `app/`(1차)은 다른 작업자 WIP — 절대 수정 금지.
- **문턱·주기·한도**: 전부 `config/pipeline.yaml` 소유. 코드 리터럴 금지.
- **문서 미러**: 로직이 코드로 확정되면 정본 `#logic`에 반영 + changelog 한 줄.

## 실행 명령

```bash
cd app-v2
uv run pytest tests/unit tests/test_web.py -q          # 681 green 유지
uv run ruff check src tests
uv run uvicorn quantinue.main:app --port 8020          # 8000 점유됨
docker compose up -d db                                 # 5445

# 통합 테스트는 '일회용 DB' 전제 — 같은 DB에 두 번 돌리면 중복키로 실패한다(정상)
docker run -d --name t -e POSTGRES_DB=quantinue -e POSTGRES_USER=quantinue \
  -e POSTGRES_PASSWORD=quantinue -p 127.0.0.1:5480:5432 postgres:17-alpine
docker exec -i t psql -q -U quantinue -d quantinue < db/schema.sql
QUANTINUE_TEST_DATABASE_URL="postgresql+asyncpg://quantinue:quantinue@127.0.0.1:5480/quantinue" \
  uv run pytest tests/integration -q
```

## app-v2 재생성이 필요할 때 (거의 없음)

```bash
rm -rf app-v2 && mkdir app-v2 \
  && git archive 6163630 app | tar -x --strip-components=1 -C app-v2 \
  && cp app/.env app-v2/.env
```
※ `.omo/`(1차 오케스트레이션 흔적 21MB)는 baseline에서 제외했다 — 다시 넣지 말 것.
