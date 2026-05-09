# 리팩토링 계획

> 상태: 과거 리팩토링 계획 문서다. 주요 구조 분리와 BigQuery 적재는 현재 코드에 반영되었지만, 이후 IRP action plan 쓰기 기능과 운영 리스크가 추가되었다. 현재 상태와 남은 개선점은 `docs/ARCHITECTURE.md`와 `audits/`의 최신 스냅샷을 우선 참고한다.

## 배경 및 목표

세 가지 문제를 해결하는 리팩토링:

1. **trade_log 이력 소실**: 현재 Google Sheets에 덮어쓰는 방식이라 실행 이력이 사라짐 → BigQuery append로 이관
2. **Slack 알림 과잉**: 실행당 6개 이상 메시지 발송 → 1개 통합 메시지로 정리
3. **코드 구조**: `allocation.py`에 비즈니스 로직과 알림 코드가 혼재 → 역할별 파일 분리

---

## 파일 구조 변경

### 신규 생성
```
src/
  bigquery/
    client.py       # BigQuery append 클라이언트
  planner.py        # PortfolioPlanner (allocation.py에서 분리)
  executor.py       # OrderExecutor (allocation.py에서 분리)
docs/
  refactoring_plan.md
  backlog.md
```

### 수정
| 파일 | 변경 내용 |
|------|-----------|
| `src/allocation.py` | StaticAllocator만 남김, slack_notify 제거 |
| `src/slack/client.py` | 통합 결과 포맷터 추가 |
| `src/sheets/client.py` | `write_worksheet` 삭제, `oauth2client` → `google-auth`. 현재는 IRP action plan 쓰기 때문에 쓰기 scope 유지 |
| `main.py` | Sheets trade_log write 제거, BigQuery write + Slack 통합 호출 |
| `pyproject.toml` | `google-cloud-bigquery` 추가, `oauth2client` 제거 |
| `src/config/env.py` | `BQ_PROJECT_ID`, `BQ_DATASET_ID` 환경변수 추가 |

---

## 1. 코드 구조 분리

### `src/planner.py` (신규)
- `PortfolioPlanner` 이동
- `slack_notify` 호출 전부 제거 → 순수 계산만 담당

### `src/executor.py` (신규)
- `OrderExecutor` 이동
- `slack_notify` 호출 전부 제거 → 순수 실행만 담당

### `src/allocation.py` (StaticAllocator만 유지)
```python
class StaticAllocator:
    def is_trading_day(self, date) -> bool:
        return self.planner.kis_client.is_trading_day(date)

    def run(self) -> pd.DataFrame:
        plan = self.planner.get_rebalancing_plan()
        result = self.executor.run_rebalancing(plan)
        return plan.merge(result, on='ticker', how='outer')
```

**제거되는 Slack 호출 (5개)**
| 위치 | 제거 대상 |
|------|-----------|
| `PortfolioPlanner._create_total_info` | 총 평가금액 알림 |
| `OrderExecutor.run_rebalancing` | sell 시작 전 예수금 |
| `OrderExecutor.run_rebalancing` | sell 이후 예수금 |
| `OrderExecutor.run_rebalancing` | buy 완료 후 예수금 |
| `StaticAllocator.run` | 리밸런싱 플랜 상세 |

---

## 2. BigQuery 이관

### `src/bigquery/client.py`
- 테이블: `{BQ_PROJECT_ID}.{BQ_DATASET_ID}.trade_log`
- `account_type` 컬럼으로 계정 구분 (단일 테이블)
- `update_dt` KST 기준, DATE 파티션 적용
- `is_already_executed(account_type, date, within_days=7)` 메서드 포함 (main.py `_is_already_executed` 이동)
- GCP 인증: 로컬 JSON 키 / Cloud Run ADC (Sheets와 동일 패턴)

### 환경변수 추가 (`.env`)
```
BQ_PROJECT_ID=...
BQ_DATASET_ID=asset_allocation
```

### Sheets trade_log 조회
Python 코드에서는 제거. Sheets에서 보고 싶으면 **Sheets "데이터 연결 추가" → BigQuery 연결** 기능 사용.

---

## 3. Slack 통합 메시지

### 메시지 구조 (실행당 1회 발송)
```
[ISA] 리밸런싱 완료 · 2026-05-08 09:30 KST
잔여 예수금: 15,234원

종목           목표%    리밸 전(차이)      리밸 후(차이)
삼성전자      30.0%   28.5% (−1.5)    29.9% (−0.1)
SK하이닉스    20.0%   21.3% (+1.3)    20.1% (+0.1)
KODEX200    50.0%   50.2% (+0.2)    50.0% ( 0.0)
```

### "리밸 후 %" 계산 (API 재호출 없이 추정)
```
after_value = current_value + direction * transaction_quantity * current_price
after_pct   = after_value / sum(after_values + remaining_cash) * 100
```
`direction`: buy=+1, sell=−1, None=0

### 파일 첨부
```python
slack_notify(title, format_rebalancing_summary(result, ...))  # 요약 메시지
slack_client.upload_files(csv_path, msg='trade_log')          # CSV 첨부
```
`SlackClient.upload_files` 는 기존 구현 재사용.

---

## 4. GoogleSheetsClient 정리

원래 계획은 Sheets를 allocation 읽기 전용으로만 쓰는 것이었다. 현재 구현은 IRP 계좌에서 `{account_type}_action_plan`을 덮어쓰므로 쓰기 권한도 사용한다.

1. `write_worksheet` 메서드 삭제
2. IRP action plan 쓰기를 위해 OAuth scope는 `https://www.googleapis.com/auth/spreadsheets` 유지
3. `oauth2client` → `google-auth` 통일 (`google.oauth2.service_account.Credentials`)
4. `pyproject.toml`에서 `oauth2client` 의존성 제거

---

## 최종 `main.py` 흐름 (목표)

```python
def main():
    args = _parse_args()
    kst_date = datetime.now(pytz.timezone('Asia/Seoul')).date()

    gs_client = GoogleSheetsClient(url=GOOGLE_SHEET_URL)
    bq_client = BigQueryClient()
    allocation_info = gs_client.get_df_from_google_sheets(f'{args.account_type}_allocation')

    allocator = StaticAllocator(account_type=args.account_type, allocation_info=allocation_info, is_test=args.test)

    should_run = args.test or args.force or (
        allocator.is_trading_day(kst_date) and
        not bq_client.is_already_executed(args.account_type, kst_date)
    )

    if should_run:
        result = allocator.run()
        slack_notify(
            f'[{args.account_type}] 리밸런싱 완료',
            format_rebalancing_summary(result, account_type=args.account_type, dt=kst_date)
        )
        slack_client.upload_files(to_csv(result), msg='trade_log')
        if not args.test:
            bq_client.append_trade_log(result, account_type=args.account_type)
```

---

## 검증

1. `--test` 실행 → Slack 메시지 1개 + CSV 첨부 확인, BQ 미기록 확인
2. `--force` 실행 → BQ에 행 추가 확인
   ```sql
   SELECT * FROM `{project}.asset_allocation.trade_log`
   ORDER BY update_dt DESC LIMIT 5
   ```
3. 7일 이내 재실행 방지 동작 확인
4. 오류 발생 시 Slack 미발송, 로그만 출력 확인
