# 아키텍처

## 전체 흐름

```
main.py
  │
  ├─ run_id = uuid4().hex                     ← 실행 단위 식별자 발급 (ARCH-003)
  │
  ├─ [실행 모드 결정] ── ARCH-002
  │    ├─ --force      → is_trading_day / is_already_executed 외부 호출 자체 스킵
  │    ├─ --test       → is_already_executed만 스킵
  │    └─ otherwise    → is_trading_day AND NOT is_already_executed
  │
  ├─ BigQueryClient.append_run_marker(run_id, status='started')   ← 주문 시작 sentinel
  │
  ├─ [리밸런싱 실행] StaticAllocator.run()
  │    ├─ ExecutionPolicy 주입 (buffer_cash, sell_to_buy_wait, buy_cash_safety_ratio) ── ARCH-007
  │    ├─ PortfolioPlanner.get_rebalancing_plan()
  │    │    ├─ KISClient.fetch_price()              ← 종목별 현재가 조회
  │    │    ├─ KISClient.fetch_domestic_total_balance()
  │    │    └─ 목표 비중 × 총 자산 → 매수/매도 수량 계산
  │    │
  │    ├─ 일반 계좌: OrderExecutor.run_rebalancing()
  │    │    ├─ sell 루프 → KISClient.create_domestic_order()
  │    │    ├─ policy.sell_to_buy_wait_seconds 대기
  │    │    └─ buy 루프  → calc_price × filled_quantity 만 잔여 차감 (ARCH-008)
  │    └─ IRP 계좌 (postfix='29'): 주문 실행 없이 action plan 반환
  │
  ├─ [결과 발송] SlackClient — 전·후 분모 분리, 후 비중은 filled_quantity 기준 (ARCH-006)
  ├─ [이력 적재] BigQueryClient.append_trade_log(result, run_id=run_id)  ← 일반 계좌
  ├─ BigQueryClient.append_run_marker(run_id, status='completed')        ← 주문 완료 sentinel
  └─ [플랜 기록] GoogleSheetsClient.overwrite_dataframe()                  ← IRP 계좌
```

KIS HTTP 호출은 모두 timeout이 적용된 단일 헬퍼(`_request`/`_parse_json`/`_check_rt_cd`)를 통과해 `KISAPIError` 도메인 예외로 변환된다 (ARCH-005).

---

## 모듈 책임

### `main.py`
실행 진입점. 실행 조건 판단, run_id 발급, run_marker 적재, 결과 수집, Slack 발송, BigQuery 적재를 조율한다. 비즈니스 로직을 직접 담지 않는다.

### `src/allocation.py` — `StaticAllocator`
`PortfolioPlanner`와 `OrderExecutor`를 조합하는 파사드. `ExecutionPolicy` (또는 `DEFAULT_EXECUTION_POLICY`)를 주입받아 두 모듈에 같은 정책을 전달한다. IRP 계좌는 executor를 호출하지 않고 plan만 반환.

### `src/planner.py` — `PortfolioPlanner`
현재 잔고와 목표 비중을 비교해 리밸런싱 계획을 수립한다. 순수 계산만 수행하며 외부 부작용(주문, 알림)이 없다. `policy.buffer_cash`로 현금 버퍼를 적용한다.

### `src/executor.py` — `OrderExecutor`
계획 DataFrame을 받아 일반 계좌의 실제 주문을 실행한다. **매도 → 정책 기반 대기 → 매수** 순서를 강제한다.

- 매도 먼저 실행해 예수금 확보 후 매수 진행
- 매수 시 `filled_quantity * calc_price`만 잔여 예수금에서 차감 → 실패 주문은 차감 0 (ARCH-008)
- `is_test=True`이면 KIS 주문 호출 자체를 스킵, `requested_quantity=0` / `filled_quantity=0`
- 결과 dict에 `requested_quantity`(KIS에 보낸 수량) / `filled_quantity`(rt_cd='0' 성공 수량) 분리 (ARCH-004). `transaction_quantity`는 deprecated alias = filled.

### `src/policy.py` — `ExecutionPolicy` (ARCH-007)
실행 정책 dataclass. `Allocator → Planner / Executor`에 주입된다.

| 필드 | 기본값 | 의미 |
|---|---|---|
| `buffer_cash` | 10,000 | 거래 전 보수적으로 빼두는 현금 버퍼 |
| `sell_to_buy_wait_seconds` | 3 | 매도 → 매수 사이 KIS 예수금 반영 대기 |
| `buy_cash_safety_ratio` | 0.99 | 매수 가능 현금에 곱하는 안전 비율 |

`StaticAllocator`는 시작 시 활성 정책 값을 로그로 남긴다.

### `src/bigquery/client.py` — `BigQueryClient`
거래 이력의 영속성을 담당한다 (ARCH-003).

- `append_trade_log(df, account_type, run_id=None)` — 모든 행에 `run_id` + `row_type='trade'` 자동 부여, WRITE_APPEND
- `append_run_marker(run_id, account_type, status)` — `row_type='run_marker'` 단일 행 적재 (started/completed sentinel)
- `is_already_executed()` — run_id 단위로 그룹핑한 후 다음 중 하나라도 만족하면 차단:
  - 정상 완료(`has_terminal=1`: trade row 또는 completed marker)
  - 진행 중인 run (started 후 `STALE_MINUTES=30분` 미경과)
  - 마이그레이션 전 옛날 행 (`run_id IS NULL`)
  → started 후 30분 지났는데 completed/trade가 없으면 좀비 run으로 자동 판정 → 다음 cron 재시도
- `LoadJobConfig.schema_update_options=ALLOW_FIELD_ADDITION` → 신규 컬럼 자동 확장
- 테이블 미존재(`NotFound`)는 첫 실행으로 간주

### `src/sheets/client.py` — `GoogleSheetsClient`
`{account_type}_allocation` 시트에서 목표 비중을 읽는다. IRP 계좌 실행 시 `{account_type}_action_plan` 시트를 생성하거나 덮어쓰므로 쓰기 권한도 필요하다. `overwrite_dataframe`은 update→trailing batch_clear 순서로 트랜잭션 안전.

### `src/kis/client.py` — `KISClient`
한국투자증권 REST API 래퍼. 인증 토큰 관리, 현재가 조회, 잔고 조회, 주문 실행을 담당한다 (ARCH-005).

- 모든 HTTP 호출은 `_request`(timeout) → `_parse_json` → `_check_rt_cd` 단계 통과
- 비즈니스/네트워크 오류는 `KISAPIError` 도메인 예외로 통일 (path/tr_id/HTTP status/rt_cd/msg1 보존, 민감 정보 미포함)
- 토큰 캐시는 JSON 형식 (`kis_token_*.json`), `access_token` + `expires_at`만 저장 (app_key/app_secret 캐시 안 함, ARCH-011)

### `src/slack/client.py` — `SlackClient`
리밸런싱 완료 후 요약 메시지를 발송한다 (ARCH-006).

- `format_rebalancing_summary` — 전·후 분모를 명시 분리. 전 = (전 주식 평가 + 전 예수금), 후 = (후 주식 평가 + 잔여 예수금). 후 비중은 **filled_quantity** 기준.
- planner의 `current_pct`는 보존 (덮어쓰지 않음)
- `format_irp_plan_summary` — IRP 전용 (수동 매매 안내 + plan 요약 + 시트 URL)

---

## 데이터 흐름

```
Google Sheets
  └─ allocation_info (ticker, weight)
        │
        ▼
  PortfolioPlanner  ◀─ ExecutionPolicy
  + KIS API (현재가, 잔고)
        │
        ▼
  plan_df (ticker, required_transaction, required_quantity, ...)
        │
        ▼
  OrderExecutor   ◀─ ExecutionPolicy
  + KIS API (주문 실행)
        │
        ▼
  trade_log df (ticker, requested_quantity, filled_quantity, transaction_quantity*, is_success, ...)
        │  *transaction_quantity는 deprecated alias = filled
        │
        ├──▶ BigQuery.trade_log 테이블 (run_id, row_type='trade'로 라벨)
        ├──▶ BigQuery.trade_log 테이블 (row_type='run_marker', status='completed' sentinel)
        └──▶ Slack 요약 (전·후 분모 분리, filled_quantity 기준 후 비중)

  IRP 계좌: plan_df
        │
        ├──▶ Google Sheets {account_type}_action_plan
        └──▶ Slack 플랜 요약 (수동 매매 안내)
```

---

## 실행 조건 판단 (ARCH-002)

```
--force    → is_trading_day / is_already_executed 외부 호출 자체 스킵, 즉시 실행
--test     → is_already_executed 스킵 (BQ 트래픽 없음). 주문 미실행, BQ 미기록
otherwise  → is_trading_day AND NOT is_already_executed (IRP는 BQ 이력 없음 → 외부 cron 빈도 제어)
```

`is_already_executed`는 BigQuery `trade_log` 테이블에서 최근 7일 이내 동일 `account_type`의 run을 조회한다 (ARCH-003).

- **차단 조건**: trade row 또는 completed marker 존재, 또는 started marker가 STALE_MINUTES(30분) 미경과
- **자동 stale**: started marker 30분 경과 + completed/trade 없음 → 좀비로 판정, 차단 해제 (다음 cron 재시도)

---

## BigQuery 테이블 스키마

테이블: `{BQ_PROJECT_ID}.{BQ_DATASET_ID}.trade_log`
파티션: `reg_date` (DATE, 일별)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `ticker` | STRING | 종목 코드 (run_marker 행은 NULL) |
| `account_type` | STRING | 계좌 타입 (ISA, PPA 등). IRP는 BigQuery 거래 이력 미적재 |
| `update_dt` | TIMESTAMP | 실행 시각 (KST) |
| `reg_date` | DATE | 파티션 기준일 |
| **`run_id`** | STRING | uuid4 hex (32자). 같은 실행의 marker + trade row는 동일 run_id 그룹 (ARCH-003) |
| **`row_type`** | STRING | `'run_marker'` (sentinel) / `'trade'` (거래 결과) / NULL (마이그레이션 전 옛날 행) |
| **`run_status`** | STRING | run_marker 행만: `'started'` / `'completed'` |
| **`requested_quantity`** | INT64 | KIS에 실제로 보낸 주문 수량 (ARCH-004) |
| **`filled_quantity`** | INT64 | rt_cd='0' 성공 시 체결 수량, 실패는 0 (ARCH-004) |
| `transaction_quantity` | INT64 | deprecated alias = filled_quantity (3개월 후 제거) |
| 그 외 | - | `plan_df` + `trade_log df` 병합 컬럼 전체 |

**스키마 자동 확장**: `LoadJobConfig.schema_update_options=[ALLOW_FIELD_ADDITION]` 적용. 새 컬럼은 첫 적재 시 BQ가 자동 추가 (ALTER 직접 실행 불필요, 마이그레이션 무중단).

---

## 외부 의존성

| 시스템 | 용도 | 인증 |
|---|---|---|
| 한국투자증권 API | 현재가 조회, 잔고 조회, 주문 실행 | App Key / App Secret (kis_api_auth.json) |
| Google Sheets | 목표 비중 읽기 + IRP action plan 쓰기 | GCP 서비스 계정 (로컬) / ADC (Cloud Run) |
| BigQuery | 거래 이력 적재 및 조회 | GCP 서비스 계정 (로컬) / ADC (Cloud Run) |
| Slack | 리밸런싱 결과 알림 | Bot OAuth 토큰 |
