# proj-asset-allocation

정적 자산 배분 전략을 자동으로 실행하는 리밸런싱 자동화 도구입니다.
한국투자증권 Open API로 국내 ETF 주문을 실행하고, BigQuery에 거래 이력을 적재하며, Slack으로 리밸런싱 요약을 발송합니다. IRP 계좌는 자동 주문을 실행하지 않고 수동 주문용 action plan을 Google Sheets에 기록합니다.

![사용 흐름](./simple_use_case_diagram.jpg)

모듈 구조와 데이터 흐름은 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)를 참고하세요.

---

## 사전 조건

실행 전 아래 항목이 준비되어 있어야 합니다.

- **한국투자증권 API 키** — App Key / App Secret / 계좌번호
- **GCP 프로젝트** — 서비스 계정 JSON (로컬 실행 시), BigQuery 데이터셋 생성 필요
- **Google Sheets** — 목표 비중 시트 (`{account_type}_allocation`) 포함한 스프레드시트
- **Slack Bot 토큰** — 결과 채널에 Bot이 초대된 상태

---

## 설정

### `.env`

```env
EXECUTE_ENV=LOCAL
KIS_API_AUTH_PATH=/path/to/kis_api_auth.json
GOOGLE_SERVICE_ACCOUNT_PATH=/path/to/gcp_service_account.json
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/...
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C...
BQ_PROJECT_ID=your-gcp-project-id
BQ_DATASET_ID=asset_allocation
```

| 변수 | 설명 |
|---|---|
| `EXECUTE_ENV` | `LOCAL`이면 서비스 계정 JSON 사용, 그 외 ADC 사용 |
| `KIS_API_AUTH_PATH` | 한국투자증권 API 인증 정보 JSON 경로 |
| `GOOGLE_SERVICE_ACCOUNT_PATH` | GCP 서비스 계정 JSON 경로 (로컬 전용) |
| `GOOGLE_SHEET_URL` | 자산 배분 설정 스프레드시트 URL |
| `SLACK_BOT_TOKEN` | Slack Bot OAuth 토큰 |
| `SLACK_CHANNEL_ID` | 결과 메시지를 발송할 채널 ID |
| `BQ_PROJECT_ID` | BigQuery 프로젝트 ID |
| `BQ_DATASET_ID` | BigQuery 데이터셋 이름 (기본값: `asset_allocation`) |

### `kis_api_auth.json`

```json
{
  "ISA": {
    "USER_ID": "...",
    "ACCOUNT_NUMBER": "XXXXXXXX-XX",
    "APP_KEY": "...",
    "APP_SECRET": "..."
  },
  "PPA": {
    "USER_ID": "...",
    "ACCOUNT_NUMBER": "XXXXXXXX-XX",
    "APP_KEY": "...",
    "APP_SECRET": "..."
  }
}
```

최상위 키가 `account_type`입니다. 실행 시 `--account_type` 인자와 일치해야 합니다.

### Google Sheets 구조

| 시트명 | 필수 컬럼 | 용도 |
|---|---|---|
| `{account_type}_allocation` | `ticker`, `weight` | 목표 자산 배분 비중 (`weight`는 0~1 소수) |
| `{account_type}_action_plan` | 자동 생성 | IRP 계좌의 수동 주문용 플랜. IRP 실행 시 덮어씀 |

`weight` 합계는 1.0 이하여야 합니다. 초과 시 실행 전 예외가 발생합니다.

일반 계좌의 거래 이력은 BigQuery `trade_log` 테이블에 자동 적재됩니다. 첫 실행 시 테이블이 없으면 자동 생성됩니다. Sheets에서 거래 이력을 조회하려면 Google Sheets의 **데이터 연결 추가 → BigQuery** 기능으로 연결하세요.

#### `trade_log` 핵심 컬럼 (ARCH-003 / ARCH-004)

| 컬럼 | 설명 |
|---|---|
| `run_id` | 실행 단위 식별자 (uuid4 hex). 같은 실행의 marker + trade row는 동일 run_id 그룹 |
| `row_type` | `'run_marker'` (sentinel) / `'trade'` (거래 결과) / NULL (마이그레이션 전 옛날 행) |
| `run_status` | run_marker 행만: `'started'` / `'completed'` |
| `requested_quantity` | KIS에 실제로 보낸 주문 수량 |
| `filled_quantity` | rt_cd='0' 성공 시 체결 수량, 실패는 0 |
| `transaction_quantity` | deprecated alias = `filled_quantity` (3개월 후 제거) |

새 컬럼은 `LoadJobConfig.schema_update_options=ALLOW_FIELD_ADDITION`으로 첫 적재 시 BQ가 자동 추가합니다 (ALTER 직접 실행 불필요).

---

## 실행

```bash
# 의존성 설치
uv sync

# 실행
uv run python main.py --account_type ISA
```

### 플래그

| 플래그 | 동작 |
|---|---|
| `--account_type` | 실행할 계좌 타입 (필수, `kis_api_auth.json` 키와 일치) |
| `--test` | KIS 조회 API는 호출하지만 실제 주문 없음, BigQuery 미기록. `is_already_executed` 조회도 스킵 |
| `--force` | **실행 조건 외부 조회(거래일/중복 실행 여부) 자체를 스킵**하고 즉시 주문 단계로 진입. 외부 API 장애와도 무관 (ARCH-002) |

### 실행 조건

아래 세 조건 중 하나를 만족해야 리밸런싱이 실행됩니다.

1. `--test` 플래그
2. `--force` 플래그 — 외부 조건 무시 (Sheets/KIS/BigQuery 초기화는 여전히 필요)
3. 오늘이 거래일이고, 최근 7일 이내 동일 `account_type`으로 진행 중이거나 완료된 run이 없음

`is_already_executed`는 BigQuery `trade_log`를 run_id 단위로 그룹핑해 다음 중 하나라도 있으면 차단합니다 (ARCH-003):
- 정상 완료된 run (trade row 또는 `run_status='completed'` marker)
- 진행 중인 run (`started` marker가 30분 미경과)

**좀비 자동 stale**: `started` marker 후 30분 지났는데 `completed`/trade row가 없으면 좀비 run으로 판정 → 차단 해제 → 다음 cron 자동 재시도 (개발자 수동 정리 불필요).

IRP 계좌(postfix='29')는 BigQuery 거래 이력을 적재하지 않으므로 `is_already_executed` 체크 자체를 스킵합니다. 빈도 제어는 외부 cron(예: Cloud Run 매달 1일)에 위임합니다.

---

## 프로젝트 구조

```
main.py                  # 진입점 — run_id 발급, 실행 조건 판단, run_marker 적재, 결과 발송
src/
├── allocation.py        # StaticAllocator — Planner/Executor 조합 파사드 + ExecutionPolicy 주입
├── planner.py           # PortfolioPlanner — 현재 잔고와 목표 비중 비교, 리밸런싱 계획 수립
├── executor.py          # OrderExecutor — 매도/매수 주문 실행, requested/filled 분리, 실패 차감 보호
├── policy.py            # ExecutionPolicy dataclass + DEFAULT_EXECUTION_POLICY (ARCH-007)
├── logger.py            # 로깅 설정 및 log_method_call 데코레이터
├── bigquery/
│   └── client.py        # BigQueryClient — append_trade_log, append_run_marker, stale 자동 처리
├── config/
│   └── env.py           # 환경변수 로딩, KISAuthConfig 파싱
├── kis/
│   ├── client.py        # KISClient — KIS REST 래퍼 + KISAPIError 도메인 예외 + 토큰 캐시(JSON)
│   └── stock_config.py  # 거래소 코드 및 통화 상수
├── sheets/
│   └── client.py        # GoogleSheetsClient — 목표 비중 읽기, IRP action plan 쓰기 (트랜잭션 안전)
└── slack/
    └── client.py        # SlackClient — 전·후 분모 분리 요약, IRP plan 요약
```

### 실행 정책 변경 (ExecutionPolicy)

기본값(`buffer_cash=10,000`, `sell_to_buy_wait_seconds=3`, `buy_cash_safety_ratio=0.99`)은 `src/policy.py`의 `DEFAULT_EXECUTION_POLICY`에 정의됩니다. 계좌별 정책을 다르게 운영하려면:

```python
from src.policy import ExecutionPolicy
from src.allocation import StaticAllocator

custom_policy = ExecutionPolicy(buffer_cash=50_000, buy_cash_safety_ratio=0.95)
allocator = StaticAllocator(account_type='ISA', allocation_info=..., policy=custom_policy)
```

`StaticAllocator`는 시작 시 활성 정책을 로그로 남깁니다.

---

## 배포 (Cloud Run)

Cloud Build로 이미지를 빌드하고 Artifact Registry에 푸시합니다.

```bash
gcloud builds submit --config cloudbuild.yaml
```

`EXECUTE_ENV`가 `LOCAL` 이외의 값이면 GCP Application Default Credentials(ADC)를 사용하므로 서비스 계정 JSON 파일이 필요 없습니다. Cloud Run Job에서 스케줄러로 주기 실행하는 구성을 권장합니다.

배포 전에는 `.dockerignore`로 `.env`, `kis_api_auth.json`, `gcp_service_account.json`, `kis_token_*` 같은 로컬 비밀 정보가 Docker 빌드 컨텍스트에 들어가지 않도록 해야 합니다.
