# 아키텍처

## 전체 흐름

```
main.py
  │
  ├─ [조건 판단]
  │    ├─ BigQueryClient.is_already_executed()   ← 7일 이내 실행 여부
  │    └─ StaticAllocator.is_trading_day()        ← 거래일 여부
  │
  ├─ [리밸런싱 실행] StaticAllocator.run()
  │    ├─ PortfolioPlanner.get_rebalancing_plan()
  │    │    ├─ KISClient.fetch_price()              ← 종목별 현재가 조회
  │    │    ├─ KISClient.fetch_domestic_total_balance()  ← 잔고 + 예수금 조회
  │    │    └─ 목표 비중 × 총 자산 → 매수/매도 수량 계산
  │    │
  │    └─ OrderExecutor.run_rebalancing()
  │         ├─ sell 루프 → KISClient.create_domestic_order()
  │         ├─ 3초 대기 (체결 후 예수금 반영)
  │         └─ buy 루프  → KISClient.create_domestic_order()
  │
  ├─ [결과 발송] SlackClient.chat_postMessage()
  └─ [이력 적재] BigQueryClient.append_trade_log()
```

---

## 모듈 책임

### `main.py`
실행 진입점. 실행 조건 판단, 결과 수집, Slack 발송, BigQuery 적재를 조율한다. 비즈니스 로직을 직접 담지 않는다.

### `src/allocation.py` — `StaticAllocator`
`PortfolioPlanner`와 `OrderExecutor`를 조합하는 파사드. 두 모듈의 입출력을 연결하고 결과 DataFrame을 병합해 반환한다.

### `src/planner.py` — `PortfolioPlanner`
현재 잔고와 목표 비중을 비교해 리밸런싱 계획을 수립한다. 순수 계산만 수행하며 외부 부작용(주문, 알림)이 없다.

- 총 자산 기준 목표 금액(`target_value`) 산출
- 목표 대비 차이(`required_value`)로 매수/매도 방향 결정
- allocation 미등록 종목은 자동으로 전량 매도 계획에 포함
- `weight` 합계 > 1.0, 음수, 중복 ticker 등 사전 검증

### `src/executor.py` — `OrderExecutor`
계획 DataFrame을 받아 실제 주문을 실행한다. **매도 → 대기 → 매수** 순서를 강제한다.

- 매도 먼저 실행해 예수금 확보 후 매수 진행
- 매수 시 연속 주문에서 잔여 예수금을 직접 추적 (API 반영 지연 대응)
- `is_test=True`이면 API는 호출하되 주문은 스킵, `transaction_quantity=0` 반환

### `src/bigquery/client.py` — `BigQueryClient`
거래 이력의 영속성을 담당한다.

- `append_trade_log()` — 실행 결과를 `trade_log` 테이블에 WRITE_APPEND
- `is_already_executed()` — 7일 이내 동일 `account_type` 실행 여부 조회로 중복 실행 방지
- 테이블 미존재(`NotFound`)는 첫 실행으로 간주. 그 외 예외는 re-raise해 네트워크 오류로 인한 중복 실행을 차단

### `src/sheets/client.py` — `GoogleSheetsClient`
읽기 전용. `{account_type}_allocation` 시트에서 목표 비중을 읽는다. 쓰기 권한 없음.

### `src/kis/client.py` — `KISClient`
한국투자증권 REST API 래퍼. 인증 토큰 관리, 현재가 조회, 잔고 조회, 주문 실행을 담당한다.

### `src/slack/client.py` — `SlackClient`
리밸런싱 완료 후 요약 메시지를 발송한다. `format_rebalancing_summary()`가 종목별 목표/전/후 비중 비교 텍스트를 생성한다.

---

## 데이터 흐름

```
Google Sheets
  └─ allocation_info (ticker, weight)
        │
        ▼
  PortfolioPlanner
  + KIS API (현재가, 잔고)
        │
        ▼
  plan_df (ticker, required_transaction, required_quantity, ...)
        │
        ▼
  OrderExecutor
  + KIS API (주문 실행)
        │
        ▼
  trade_log df (ticker, transaction_quantity, is_success, ...)
        │
        ├──▶ BigQuery.trade_log 테이블 (이력 누적)
        └──▶ Slack 요약 메시지 (종목별 전/후 비중)
```

---

## 실행 조건 판단

```
--test     → 무조건 실행 (주문 스킵, BQ 미기록)
--force    → 무조건 실행 (주문 실행, BQ 기록)
otherwise  → is_trading_day AND NOT is_already_executed(within_days=7)
```

`is_already_executed`는 BigQuery `trade_log` 테이블에서 최근 7일 이내 동일 `account_type` 행이 있으면 `True`를 반환한다. 주간 1회 리밸런싱 기준으로 설계되어 있으며, 빈도를 변경하려면 `within_days` 인자를 조정한다.

---

## BigQuery 테이블 스키마

테이블: `{BQ_PROJECT_ID}.{BQ_DATASET_ID}.trade_log`
파티션: `reg_date` (DATE, 일별)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `ticker` | STRING | 종목 코드 |
| `account_type` | STRING | 계좌 타입 (ISA, PPA 등) |
| `update_dt` | TIMESTAMP | 실행 시각 (KST) |
| `reg_date` | DATE | 파티션 기준일 |
| 그 외 | - | `plan_df` + `trade_log df` 병합 컬럼 전체 |

---

## 외부 의존성

| 시스템 | 용도 | 인증 |
|---|---|---|
| 한국투자증권 API | 현재가 조회, 잔고 조회, 주문 실행 | App Key / App Secret (kis_api_auth.json) |
| Google Sheets | 목표 비중 읽기 | GCP 서비스 계정 (로컬) / ADC (Cloud Run) |
| BigQuery | 거래 이력 적재 및 조회 | GCP 서비스 계정 (로컬) / ADC (Cloud Run) |
| Slack | 리밸런싱 결과 알림 | Bot OAuth 토큰 |
