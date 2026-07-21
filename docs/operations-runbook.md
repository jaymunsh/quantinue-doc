# 운영 매뉴얼 — 켜고, 보고, 끄는 법

> 서버 없이 맥 한 대에서 사람이 켜고 끄는 운용을 전제로 쓴다.
> **이 문서만 보고 처음부터 띄울 수 있어야 한다.** 안 되면 그건 문서 결함이다.
>
> 코드·설계 문서는 `docs/mvp2-planning/`에 있다. 여기는 **운영만** 다룬다.

---

## 0. 한 줄 요약

```bash
cd ~/Documents/ClaudeCode/quantinue-v2/app-v2
docker start app-v2-db-1          # DB (이미 떠 있으면 생략)
./scripts/run_observation.sh      # 앱
```

브라우저에서 **http://127.0.0.1:8020/admin** → `admin` / `quantinue-admin`

끝이다. 나머지는 이 셋을 풀어 쓴 것이다.

---

## 1. 켜기

### 1-1. DB 먼저

앱보다 DB가 먼저 떠 있어야 한다. 안 떠 있으면 앱이 기동 중에 죽는다.

```bash
docker start app-v2-db-1
docker ps --format '{{.Names}}\t{{.Ports}}' | grep app-v2-db
# 기대: app-v2-db-1   127.0.0.1:5445->5432/tcp
```

⚠️ **포트 5445가 맞는지 꼭 본다.** `app-db-1`(5444)은 1차 개발용이고 **다른
작업자 것**이다. 거기에 쓰면 남의 데이터를 건드린다.

### 1-2. 앱

```bash
cd ~/Documents/ClaudeCode/quantinue-v2/app-v2
./scripts/run_observation.sh
```

터미널을 계속 열어둬야 한다. 닫으면 앱이 죽는다. 백그라운드로 두려면:

```bash
nohup ./scripts/run_observation.sh > /dev/null 2>&1 &
```

로그는 `app-v2/observation.log`에 쌓인다. 실시간으로 보려면 `tail -f observation.log`.

### 1-3. 떴는지 확인

```bash
curl -s http://127.0.0.1:8020/health
# 기대: {"status":"ok","broker_mode":"mock","llm_mode":"local"}
```

`llm_mode`가 **`local`**이면 실 LLM으로 판단한다. `mock`이면 고정값이라
가볍지만 판단이 가짜다 — 관측 목적이면 `local`이 맞다.

⚠️ `local`이면 **oMLX(127.0.0.1:8888)가 떠 있어야 한다.** 안 떠 있으면 분석
잡만 실패하고 나머지는 정상으로 돈다(그리고 텔레그램으로 알림이 온다).

---

## 2. 매일 보기

### 2-1. 가만히 있어도 텔레그램이 온다

| 언제 | 무엇 |
|---|---|
| 잡이 실패한 즉시 | `❌ news 실패 · 슬롯 2026-07-21` |
| 평일 **KST 13:20 전후** | `✅ 2026-07-21 슬롯 · 잡 12/12 성공 · 신규 매수 3건` |

**평일 오후에 아무것도 안 오면 그게 신호다** — 앱이 안 떴거나 맥이 꺼져
있었다는 뜻이다. 알림을 보내는 주체가 앱이라, 앱이 죽으면 침묵한다.

주말·공휴일엔 안 온다. 거래일이 아니면 잡 자체가 안 돈다. **정상이다.**

시각이 KST 13:00인 이유: 슬롯은 **뉴욕 날짜** 기준이고 뉴욕 자정이 KST
13:00이다. 일일 안내는 체인 맨 끝이라 앞의 잡 12개가 끝난 뒤에 온다
(실 LLM 분석이 15분쯤 걸린다).

### 2-2. 직접 볼 때

| 화면 | 링크 | 무엇을 보나 |
|---|---|---|
| 관제실 | http://127.0.0.1:8020/admin | 잡 체인이 **어디서 끊겼나** · 계좌 총람 |
| 계좌 관리 | http://127.0.0.1:8020/admin/accounts | 개설 · 성향 · 정지 |
| 내 계좌 | http://127.0.0.1:8020/me | 유저가 보는 화면 |

관제실에서 볼 것 셋: ① 잡 12개가 다 `succeeded`인가(아니면 화면이 처음 끊긴
잡을 지목한다) ② 슬롯 탭으로 빠진 날이 있는가 ③ 계좌 총람에서 체결이 느는가.

### 2-3. 계정

| 아이디 | 비밀번호 | 볼 수 있는 것 |
|---|---|---|
| `admin` | `quantinue-admin` | 관제실 · 계좌 관리 (`/me`는 404가 정상 — 계좌가 없다) |
| `user1`~`user5` | `quantinue-user` | 자기 계좌만 (`/admin`은 404가 정상) |

새 계정은 **화면에서** 만든다 — `/admin/accounts` 하단의 계좌 개설 폼.
셀프 가입은 없다.

---

## 3. 끄기

```bash
pkill -f "uvicorn quantinue.main:app"
```

⚠️ **끄기 전에 잡이 도는 중인지 본다.** 도는 중에 끄면 그 슬롯이 `running`
으로 굳고, 재시도는 `failed`만 집으므로 **그 잡은 그날 영영 안 돈다.**

관제실 잡 체인에서 `running` 상태가 있는지 보면 된다. 있으면 끝날 때까지
기다리거나, 껐다면 아래 4-1로 푼다.

DB는 굳이 안 꺼도 된다(유휴 시 CPU 0%·메모리 200MB). 끄려면
`docker stop app-v2-db-1`.

---

## 4. 문제가 생겼을 때

### 4-1. 잡이 `running`에서 안 넘어간다

앱을 잡 도중에 끈 흔적이다. **관제실에서 그 잡 옆의 `잠금 해제` 버튼을 누른다.**
버튼은 잡을 실행하지 않고 잠금만 푼다 — 러너가 다음 틱(최대 60초)에 스스로
다시 집는다.

### 4-2. 텔레그램이 안 온다

1. 앱이 떠 있나 — `curl -s http://127.0.0.1:8020/health`
2. 키가 들어 있나 — `app-v2/.env`의 `QUANTINUE_TELEGRAM_BOT_TOKEN`·`_CHAT_ID`.
   **둘 중 하나라도 비면 알림 경로 자체가 안 만들어진다**(의도된 동작).
3. 오늘 이미 보냈나 — 일일 안내는 하루 한 번이다. 관제실 잡 체인에
   `daily_summary`가 `succeeded`면 이미 갔다.

### 4-3. 한 화면만 500이 난다 (다른 화면은 멀쩡)

**템플릿과 파이썬 코드의 버전이 어긋난 것이다.** 원인은 둘의 반영 시점이
다르기 때문이다:

| | 실행 중인 앱에 언제 반영되나 |
|---|---|
| 템플릿(`.html`) | **즉시** — Jinja가 파일이 바뀌면 다시 읽는다 |
| 파이썬 코드 | 재기동해야 |
| CSS | 재기동해야 (import 시점에 인라인된다) |

그래서 템플릿이 **새 필드**를 참조하는데 코드가 옛것이면 그 화면만 죽는다.
실제로 밟았다(2026-07-21): 작동 로그 템플릿에 쪽 나누기(`total_pages`)를
넣자 관측 인스턴스가 그 필드 없는 옛 모델로 렌더하다 500.

```
tail -40 app-v2/observation.log | grep -A 3 UndefinedError
# jinja2.exceptions.UndefinedError: ... has no attribute 'total_pages'
```

**고치는 법은 재기동뿐이다.** 잡이 도는 중이 아닌지 확인하고 §1-2대로 다시 띄운다.
⚠️ 코드를 고치는 동안 관측 인스턴스가 이 상태에 빠질 수 있다 — 템플릿을
고쳤으면 관측 인스턴스도 그날 안에 재기동할 것.

### 4-4. 화면이 안 열린다 / 로그인이 안 된다

- **포트를 먼저 본다**: `lsof -nP -iTCP:8020 -sTCP:LISTEN`
- 세션 쿠키가 꼬였으면 브라우저에서 로그아웃 후 다시 로그인
- `tb_user`에 행이 있어 관제실은 **로그인을 요구한다**. 예고된 동작이다.

### 4-5. 하루가 통째로 비었다

맥이 꺼져 있었을 가능성이 높다. **그날 안에 앱을 켜면 밀린 잡이 뒤늦게 돈다** —
주기 판정이 요일이 아니라 "마지막 성공으로부터 경과일"이라 그렇다. 뉴욕 날짜
기준이라 창이 넓다(KST 대략 13시 ~ 다음날 13시).

---

## 5. 코드를 고칠 때

⚠️ **관측 인스턴스는 그대로 두고 다른 포트로 띄운다.** 코드 작업용은
`--reload`가 필요한데, 리로드는 프로세스를 다시 띄우고 그게 잡 도중이면
슬롯이 굳는다(4-1).

```bash
cd ~/Documents/ClaudeCode/quantinue-v2/app-v2
QUANTINUE_DATA_MODE=public QUANTINUE_DATABASE_MODE=postgres \
QUANTINUE_DATABASE_URL="postgresql+asyncpg://quantinue:quantinue@127.0.0.1:5445/quantinue" \
uv run uvicorn quantinue.main:app --port 8021 --reload \
  --reload-dir src/quantinue --reload-include '*.css' --reload-include '*.html'
```

⚠️ **`--reload`가 없으면 CSS를 고쳐도 화면이 안 바뀐다** — `dashboard.css`는
import 시점에 한 번만 읽어 HTML에 인라인된다.

### 검증

```bash
cd app-v2
uv run pytest tests/unit tests/test_pipeline_dashboard.py tests/test_my_account.py -q
uv run ruff check src tests scripts      # 파이프(| tail) 걸지 말 것 — 종료코드가 가려진다
./scripts/scan_secrets.sh                # 커밋 전
```

통합 테스트는 **일회용 DB**가 필요하다(같은 컨테이너에서 두 번 못 돌린다 —
멱등 가드가 옛 행을 지켜내 실패를 가린다):

```bash
docker run -d --name qn-itest -e POSTGRES_USER=quantinue -e POSTGRES_PASSWORD=quantinue \
  -e POSTGRES_DB=quantinue -p 127.0.0.1:5480:5432 postgres:16
docker exec -i qn-itest psql -q -U quantinue -d quantinue < db/schema.sql
QUANTINUE_TEST_DATABASE_URL="postgresql+asyncpg://quantinue:quantinue@127.0.0.1:5480/quantinue" \
  uv run pytest tests/integration -q -p no:unraisableexception
docker rm -f qn-itest
```

---

## 6. 알아두면 좋은 것

- **자원**: 앱 24MB · DB 201MB · 유휴 CPU 0%. 부담은 LLM이고 **하루 15분**이다.
- **체결은 진짜 돈이 아니다.** 로컬 시뮬(MockBroker)이고 시세만 실물(Alpaca)이다.
  실제 주문이 브로커로 나가는 것은 아직 안 한다.
- **`.env`를 `.env.example`로 덮어쓰지 않는다.** 키가 다 날아간다.
- **`app/`(1차)은 다른 작업자 것이다.** 건드리지 않는다.
