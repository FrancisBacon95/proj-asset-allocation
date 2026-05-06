# 이슈: 리밸런싱 매수 시 "주문가능금액을 초과" 오류

**발생일**: 2026-05-06  
**계좌**: ISA  
**증상**: 매수 주문 9건 중 1건(TIGER KOFR)만 성공, 나머지 8건은 모두 `주문가능금액을 초과 했습니다` 실패

---

## 1. 현상

```
[ISA] sell 시작 전 예수금:  10,074,877원
[ISA] sell 이후 예수금:     10,074,877원  (매도 없음)
[ISA] buy 완료 후 예수금:    9,631,669원  (KOFR 443,200원만 사용)
```

| 종목 | 주문 수량 | enable_qty | 결과 |
|------|----------|------------|------|
| TIGER KOFR | 4 | 69 | ✅ 성공 |
| ACE T-Bond | 90 | 985 | ❌ 주문가능금액 초과 |
| KODEX T-note | 57 | 552 | ❌ |
| (이하 6종목) | ... | ... | ❌ |

`enable_qty`는 충분해 보이지만 API는 거부 → **코드가 사용 가능한 금액을 과대평가**하고 있음.

---

## 2. 원인 분석

### 버그가 도입된 커밋

`3476b8b` — *fix: 연속 매수 시 잔여 현금 추적 및 당일 매도 대금 반영 (2026-03-27)*

해당 커밋에서 매수 가능 금액 계산 기준이 변경됨:

**변경 전 (정상)**
```python
# API가 직접 계산한 주문 가능 금액 사용
available_amt = (int(result['nrcvb_buy_amt']) + int(result['ruse_psbl_amt'])) * 0.99
```

**변경 후 (버그)**
```python
# 잔고 조회 API의 예수금 총금액 사용
cash_balance = available_cash  # dnca_tot_amt + thdt_sll_amt - thdt_buy_amt
available_amt = cash_balance * 0.99
```

### 왜 문제가 생기는가

| 필드 | 값 | 설명 |
|------|-----|------|
| `dnca_tot_amt` (예수금총금액) | ~10,074,877원 | 잔고 조회 API가 반환하는 예수금 총액 |
| `nrcvb_buy_amt` (미수 없는 매수 가능 금액) | **추정 ~600K원** | 주문 API가 실제 허용하는 금액 |

`dnca_tot_amt`와 `nrcvb_buy_amt` 사이에 큰 차이가 있을 경우, 코드는 10M이 가용하다고 판단해 full quantity로 주문을 내지만, 실제 API는 600K 수준만 허용하여 거부한다.

**TIGER KOFR만 성공하는 이유**: 443,200원이 실제 주문 가능 금액 범위 안이었기 때문. 체결 후 잔여 주문가능금액이 소진되어 이후 모든 주문 거절.

### 왜 `dnca_tot_amt > nrcvb_buy_amt`인가 (미확인)

`3476b8b` 커밋의 원래 의도는 올바름: **당일 ETF 매도 대금은 T+2 결제이지만 당일 매수에 사용 가능**한데, `nrcvb_buy_amt`가 이를 반영하지 않아 마지막 종목이 과소 매수되는 문제를 수정하려 했음.

그러나 `dnca_tot_amt`가 왜 `nrcvb_buy_amt`보다 크게 높은지는 추가 확인 필요:
- CMA/RP 잔고 (계좌에 없다고 확인됨 → 이 원인은 아님)
- 이전 실행의 미체결 주문이 잔고를 점유하고 있는 경우 ← **유력 후보**
- ISA 계좌 특유의 주문가능금액 산정 방식 차이

### 거래 로그로 확인된 사항 (2026-05-06)

거래 로그(`asset_allocation - ISA_trade_log.csv`) 분석 결과 `enable_qty`가 `required_qty`의 약 10배 수준임을 확인:

| 종목 | required_qty | enable_qty | 비율 |
|------|-------------|-----------|------|
| ACE T-Bond | 90 | 985 | ~11x |
| KODEX T-note | 57 | 552 | ~10x |
| KODEX India | 66 | 451 | ~7x |

`enable_qty`는 `remaining_cash`(= `dnca_tot_amt` ≈ 10M)를 기준으로 계산됐으나, 실제 API 허용 금액은 ~600K에 불과했음. 이 10배 과대평가가 연쇄 실패의 직접 원인.

계좌에 CMA/RP 없음, 미수 사용 안 함이 확인된 상태에서도 `nrcvb_buy_amt << dnca_tot_amt`가 발생 → **이전 실행의 미체결 주문 잔류**가 가장 유력한 원인.

---

## 3. 수정 내역

### `src/kis/client.py` — `fetch_buy_orderable_cash()` 추가

```python
def fetch_buy_orderable_cash(self, ticker: str) -> int:
    """
    nrcvb_buy_amt + ruse_psbl_amt + thdt_sll_amt 반환.
    dnca_tot_amt 대신 API 직접 계산 값 사용.
    """
```

- API의 `nrcvb_buy_amt`(미수 없는 매수 가능 금액) + `ruse_psbl_amt`(재사용 가능 금액)를 기준으로 사용
- 당일 매도 대금(`thdt_sll_amt`)을 수동으로 가산 (T+2 미반영분 보정)
- `dnca_tot_amt`와의 차이를 로그에 출력 → 다음 실행 시 원인 진단 가능

### `src/allocation.py` — `run_rebalancing()` 수정

```python
# 변경 전
remaining_cash = cash_before_buy  # dnca_tot_amt 기반

# 변경 후
remaining_cash = self.kis_client.fetch_buy_orderable_cash(first_ticker)  # nrcvb 기반
```

### `src/allocation.py` — `_get_orderable_qty()` fallback 수정

`available_cash=None`일 때 `fetch_domestic_cash_balance()` 대신 `nrcvb_buy_amt + ruse_psbl_amt` 사용.

---

## 4. 수정 적용 후 관찰 사항

수정 코드 적용 후 재실행 시 예수금 약 90만원 잔여 확인.

### 확인이 필요한 로그 라인

다음 실행 시 로그에서 아래 라인 확인:

```
buy_orderable: nrcvb=???, ruse=???, thdt_sll=??? → ???원 (dnca_tot=...)
```

| 케이스 | 의미 | 대응 |
|--------|------|------|
| `nrcvb ≈ dnca_tot` | 두 값이 거의 같음 → 90만원 잔여는 수량 절사(floor) 누적 | 잔여 현금 재배분 로직 추가 검토 |
| `nrcvb << dnca_tot` (차이 ~90만원) | ISA 계좌 고유 제약 또는 미체결 잔류 | 미체결 주문 여부 확인, 계좌 구조 점검 |

---

## 5. 참고

- `nrcvb_buy_amt`: "미수 없는 매수 가능 금액" — KIS `TTTC8908R` (inquire-psbl-order) 응답 필드
- `ruse_psbl_amt`: "재사용 가능 금액" — 당일 매도 체결금 중 즉시 재사용 가능한 금액
- `thdt_sll_amt`: "금일 매도 금액" — `TTTC8434R` (inquire-balance) output2 필드, T+2 미반영분
- `dnca_tot_amt`: "예수금 총금액" — 주문가능금액과 다를 수 있음
