# KIS 매수 가능 현금·예수금 조회 가이드

> KIS API에서 "지금 매수에 쓸 수 있는 돈이 얼마인가"를 정확히 알기 위한 레퍼런스. 한국 주식의 T+2 결제 주기와 KIS 응답 필드의 의미를 함께 정리한다. 이 프로젝트의 잔고/매수 로직을 손볼 때 가장 먼저 펼쳐봐야 하는 문서.

---

## 1. 한국 주식 결제 주기 기초 (T+2)

한국 주식 시장은 **T+2 결제** 방식이다. 거래 체결일을 T(또는 T+0)이라 할 때:

| 시점 | 의미 |
|---|---|
| **T(T+0)** | 매매가 체결된 거래일 — 주식·돈이 "예약"되었지만 아직 청산되지 않음 |
| **T+1** | 다음 영업일 — 일부 정산이 반영되는 시점 |
| **T+2** | 매매일 + 2영업일 — 매도 대금이 출금 가능해지는 실제 결제일 |

### 매도/매수의 실효 흐름
- **매도 체결(T)** → 주식은 즉시 차감, 매도 대금은 **T+2에 정산**되어 출금/이체 가능해짐. 다만 그 사이에도 **재사용(다른 종목 매수에 활용)** 은 보통 가능.
- **매수 체결(T)** → 주식은 즉시 입고, 매수 대금은 **T+2에 출금**됨.

핵심 함의: "지금 계좌에 보이는 예수금"과 "지금 매수에 실제 쓸 수 있는 돈"은 다를 수 있다. 이전 매도 대금이 T+1, T+2에 차례로 들어올 예정이라면, 이를 매수에 미리 활용하느냐가 운용의 갈림길이다.

---

## 2. KIS의 두 가지 조회 엔드포인트

매수 가능 금액은 **두 가지 다른 API**로 조회할 수 있고, 둘은 다른 시각으로 같은 계좌를 본다.

### A. 주식잔고조회 (`inquire-balance`)
- **TR_ID**: `TTTC8434R` (실전) / `VTTC8434R` (모의)
- **URL**: `/uapi/domestic-stock/v1/trading/inquire-balance`
- **목적**: 계좌 전체 스냅샷 — 보유 종목 + 현금(예수금) + 평가금액
- **`output2`** 에 D+0/D+1/D+2 시점별 예수금이 모두 들어 있음
- 코드: `src/kis/client.py`의 `_domestic_balance_page()`

### B. 매수가능조회 (`inquire-psbl-order`)
- **TR_ID**: `TTTC8908R`
- **URL**: `/uapi/domestic-stock/v1/trading/inquire-psbl-order`
- **목적**: **특정 종목**에 대한 주문 가능 금액/수량 — 종목별 증거금률, 미수 여부 등을 반영
- 종목별로 호출해야 하므로 잔고조회보다 비용이 큼
- 코드: `src/kis/client.py`의 `fetch_domestic_enable_buy()`

요약하면:
- **A는 "내 계좌에 돈이 얼마 있나"** (계좌 단위, 미래 시점 예측 포함)
- **B는 "이 종목을 지금 얼마치 살 수 있나"** (종목 단위, 보수적 기준)

---

## 3. 필드 사전 — 잔고조회 `output2`

`주식잔고조회[v1_국내주식-006]` 공식 문서 기준. 매수 가능 현금 산정에 직접 관계되는 필드만 추렸다.

| 필드 | 한글명 | 의미 |
|---|---|---|
| **`dnca_tot_amt`** | 예수금총금액 | **D+0 예수금**. 지금 이 순간 결제까지 완료된 현금. |
| **`nxdy_excc_amt`** | 익일정산금액 | **D+1 예수금**. 내일 시점에 보유하게 될 예상 예수금. |
| **`prvs_rcdl_excc_amt`** | 가수도정산금액 | **D+2 예수금**. 모레(T+2) 시점에 보유하게 될 예상 예수금. 즉 현재 미정산 거래가 모두 청산된 후의 잔액. |
| `cma_evlu_amt` | CMA평가금액 | CMA 연동 잔액. 즉시 주문 불가일 수 있음. |
| `thdt_buy_amt` | 금일매수금액 | 오늘 체결된 매수 대금 합계. |
| `thdt_sll_amt` | 금일매도금액 | 오늘 체결된 매도 대금 합계. T+2 미반영. |
| `bfdy_buy_amt` | 전일매수금액 | 어제 체결된 매수 대금 합계. |
| `bfdy_sll_amt` | 전일매도금액 | 어제 체결된 매도 대금 합계. |
| `tot_evlu_amt` | 총평가금액 | **유가증권 평가금액 + D+2 예수금**. 즉 `prvs_rcdl_excc_amt` 기반의 총자산. |
| `nass_amt` | 순자산금액 | 대출/융자 차감 후 순자산. |

> **포인트**: `nxdy_excc_amt`, `prvs_rcdl_excc_amt`는 **누적이 아니라 각 시점의 잔액**이다. "D+2 예수금 = D+0 + D+1 + D+2"가 아니라 "T+2 시점에 결제 완료되어 있을 잔액 그 자체". 따라서 단순 덧셈을 하면 절대 안 된다.

> **주의**: KIS 공식 설명에서 `tot_evlu_amt = 유가증권 평가금액 + D+2 예수금`이라고 명시하고 있다 (line 129). 즉 KIS가 공식적으로 인정하는 "총자산"의 현금 부분도 `prvs_rcdl_excc_amt` 기반이다.

---

## 4. 필드 사전 — 매수가능조회 `output`

`퇴직연금 매수가능조회[v1_국내주식-034]` 문서가 직접 참고 가능. ISA 계좌의 `TTTC8908R` 응답도 거의 동일한 필드 구조.

| 필드 | 한글명 | 의미 |
|---|---|---|
| **`nrcvb_buy_amt`** | 미수 없는 매수 가능 금액 | **실제 매수 한도** (ISA 한정). KIS 앱의 "주문가능"과 일치. 산식 추정: `ord_psbl_cash + ruse_psbl_amt − 수수료/세금 buffer`. **퇴직연금 응답에는 부재 — 4.2 참조.** |
| **`ruse_psbl_amt`** | 재사용가능금액 | 미정산 매도 대금 중 추가 매수에 즉시 활용 가능한 금액. |
| **`ord_psbl_cash`** | 주문가능현금 | **D+0 시점 결제 완료된 현금만** (재사용가능액 제외). 이름과 달리 실제 매수 한도가 아님 — 4.1 참조. ISA·퇴직연금 모두 존재. |
| `psbl_qty_calc_unpr` | 가능수량계산단가 | 가능 수량 산정에 쓰이는 단가 (시장가일 때 KIS 내부 추정 보수단가). |
| `max_buy_amt` | 최대매수금액 | `nrcvb_buy_amt`와 동일 값으로 응답되는 것이 관측됨. |
| `max_buy_qty` | 최대매수수량 | `floor(nrcvb_buy_amt / psbl_qty_calc_unpr)`. |

> **주의**: 이 API는 **"내 계좌에서 이 종목을 지금 실제 주문하면 얼마까지 가능한가"** 를 보수적으로 답한다. 종목별 증거금률·미수 정책이 반영되어, 잔고조회의 `prvs_rcdl_excc_amt`보다 작게 나오는 경우가 흔하다.

### 4.1 운영자 관점: 거래에는 `nrcvb_buy_amt`만 쓰면 된다

**한 줄 요약**: 매수 한도는 항상 `nrcvb_buy_amt`(=KIS 앱 "주문가능"). 다른 필드의 값이나 차이는 신경 쓸 필요 없다.

ISA 계좌 실측 (2026-05, `test/dump_isa_orderable.py`):

| 필드 | 값 | 역할 |
|---|---:|---|
| `dnca_tot_amt` (D+0 예수금) | 327,867 | 결제 완료 현금 |
| `ord_psbl_cash` (주문가능현금) | 327,867 | **이름과 달리 D+0과 동일 — 함정** |
| `ruse_psbl_amt` (재사용가능액) | 189,501 | 미정산 매도 대금 중 매수 재사용분 |
| `prvs_rcdl_excc_amt` (D+2 예수금) | 519,685 | 총자산 산정용 (`fetch_domestic_cash_balance`) |
| **`nrcvb_buy_amt` (실제 매수 한도)** | **510,632** | **거래용 (`fetch_buy_orderable_cash`)** |

#### 왜 산식·차이를 몰라도 되는가
- `nrcvb_buy_amt`와 `prvs_rcdl_excc_amt`(D+2)의 차이는 KIS가 종목·시점·미정산 거래에 따라 매번 동적으로 산정하는 안전마진이다 (수수료·세금·미수 정책 등). **정확한 산식은 KIS 비공개.**
- 본 시스템의 `src/kis/client.py:fetch_buy_orderable_cash()`는 매 호출마다 KIS의 `nrcvb_buy_amt`를 직접 받아 그대로 반환한다. 마진 값을 추정·하드코딩하지 않으므로 KIS 산정 변화가 자동 반영된다.

#### 함정 — `ord_psbl_cash`는 실제 매수 한도가 아니다
한글명 "주문가능현금"은 오해를 일으킨다. 값은 `dnca_tot_amt`(D+0)와 정확히 일치하며, 미정산 매도 대금의 재사용분이 빠져 있어 실제 매수 한도(`nrcvb_buy_amt`)보다 작다. **`ord_psbl_cash`만 보고 매수 가능 금액을 판단하면 안 된다.**

> **참고 (운영 무관)**: 차이 분해(예: 519,685 − 510,632 = 9,053원)는 `test/dump_isa_orderable.py`로 시점별 관측 가능. 산수상 `nrcvb ≈ ord_psbl_cash + ruse_psbl_amt − KIS 동적 마진`이지만, 이 마진의 산식·값은 알 필요가 없다.

**공식 문서 부재**: `퇴직연금 매수가능조회[v1_국내주식-034]` doc은 `ord_psbl_cash` 필드명만 명시하고 정의는 없다. ISA용 inquire-psbl-order는 공식 doc 자체가 없다. 응답 구조는 ISA·IRP가 대체로 비슷하지만 `nrcvb_buy_amt` 등 일부 필드 유무에서 차이가 있으므로 주의 (4.2 참조).

### 4.2 IRP 매수 한도 — `max_buy_amt` 폴백 (PPA·연금저축은 ISA와 동일 처리)

#### 계좌 종류별 분류 (postfix 기준)

| postfix | 계좌 종류 | 처리 방식 |
|---|---|---|
| `01` | ISA·일반 위탁 | 일반 엔드포인트 (TTTC8434R, TTTC8908R 등) |
| **`22`** | **연금저축(PPA)** | **ISA와 동일 — 일반 엔드포인트 사용** (실측 확인) |
| `29` | IRP(개인형 퇴직연금) | KIS 퇴직연금 전용 엔드포인트 (TTTC2208R, TTTC0503R 등) |

PPA(연금저축)는 명칭상 "연금"이지만 KIS API 관점에서는 ISA와 같은 일반 위탁 엔드포인트로 처리된다. IRP만 퇴직연금 전용 엔드포인트로 응답한다.

#### IRP의 `inquire-psbl-order` 응답 차이

IRP의 응답에는 `nrcvb_buy_amt` 필드가 **존재하지 않는다.** 응답 키는 `ord_psbl_cash`, `ruse_psbl_amt`, `psbl_qty_calc_unpr`, `max_buy_amt`, `max_buy_qty`의 5개뿐이며, ISA에 있는 `nrcvb_buy_amt`/`nrcvb_buy_qty`/`ord_psbl_sbst`/`fund_rpch_chgs`/`cma_evlu_amt`/`ovrs_re_use_amt_wcrc`/`ord_psbl_frcr_amt_wcrc`는 부재.

이는 **IRP 계좌가 미수 거래를 법적으로 사용할 수 없기 때문**으로 해석된다 — "미수 없는 매수 가능 금액"이라는 개념 자체가 자명하므로 KIS가 별도 필드로 노출할 필요가 없다.

#### 매수 한도 필드 매핑

| 계좌 | 매수 한도 필드 | 의미 |
|---|---|---|
| ISA·연금저축(postfix='01'/'22') | `nrcvb_buy_amt` | 미수 없는 매수 가능 금액 (KIS 앱 "주문가능") |
| IRP(postfix='29') | `max_buy_amt` | 최대 매수 금액 — 미수 불가이므로 ISA의 nrcvb와 의미적 등가 |

**실측 검증**: ISA 계좌에서 `nrcvb_buy_amt == max_buy_amt`로 모든 ticker·주문구분에서 동일 값이 응답됨 (`test/dump_isa_orderable.py`). 두 필드가 같은 한도를 다른 이름으로 노출하는 형태로 추정된다.

코드: `src/kis/client.py:fetch_buy_orderable_cash`가 `_is_pension()` (= `acc_no_postfix == '29'`) 분기로 처리.

---

## 5. 시나리오로 보는 필드 동작

### 시나리오 ①: 거래가 전혀 없는 날
- `dnca_tot_amt = nxdy_excc_amt = prvs_rcdl_excc_amt` (세 값 동일)
- `thdt_buy_amt = thdt_sll_amt = 0`
- 어떤 필드를 써도 결과는 같음.

### 시나리오 ②: 어제 100만원어치 매도, 오늘 거래 없음 (현재 D+1)
- 어제(T) 매도 → 모레(어제 + T+2 = 오늘 + T+1)에 대금 정산
- 오늘 시점:
  - `dnca_tot_amt`: 매도 전 예수금 (예: 49만원) — 아직 정산 안 됨
  - `nxdy_excc_amt`: 49만 + 100만 = 149만 (내일 정산 후 잔액)
  - `prvs_rcdl_excc_amt`: 149만 (모레도 동일)

### 시나리오 ③: 오늘 100만원어치 매도 + 50만원어치 매수
- `dnca_tot_amt`: 변동 없음 (아직 모두 미정산)
- `thdt_sll_amt = 1,000,000`, `thdt_buy_amt = 500,000`
- `nxdy_excc_amt`: T+1 시점 예수금 — 어제까지의 정산 결과 반영
- `prvs_rcdl_excc_amt`: T+2 시점 예수금 = `dnca_tot_amt + 매도분 - 매수분 + 기타 정산`
  = 대략 `dnca_tot_amt + thdt_sll_amt - thdt_buy_amt + (이전 미정산 매도분)`

### 시나리오 ④: 사용자의 실제 케이스 (90만 vs 49만)
- `dnca_tot_amt ≈ 490,000` (지금 정산 완료된 예수금)
- `prvs_rcdl_excc_amt ≈ 900,000` (T+2 시점 예수금)
- 격차 41만원 = **이미 체결된 매도의 미정산 대금**이 T+1, T+2에 들어올 예정
- `inquire-psbl-order`의 `nrcvb_buy_amt`도 `dnca_tot_amt`에 가깝게 보수적으로 응답하고 있을 가능성이 높음

---

## 6. "매수 가능 현금"을 어떻게 정의할 것인가

같은 계좌라도 정의에 따라 답이 다르다. 세 가지 흔한 접근:

### 접근 A — 보수적 (D+0 기준)
```
available = dnca_tot_amt + thdt_sll_amt - thdt_buy_amt
```
- 의미: "지금 결제 완료된 현금 + 오늘 거래 반영"
- 장점: 가장 안전. 미수·미정산 리스크 없음.
- 단점: T+1, T+2에 들어올 매도 대금을 활용하지 못함 → 매수 여력 과소.
- 현재 `fetch_domestic_cash_balance()`는 이 방식이 아니라 `prvs_rcdl_excc_amt`를 그대로 반환한다.

### 접근 B — 중도적 (KIS 매수가능조회 기반)
```
available = nrcvb_buy_amt + ruse_psbl_amt + thdt_sll_amt
```
- 의미: KIS API가 보수적으로 답한 "주문 가능액" + 오늘 매도 대금
- 장점: KIS 공식 가이드를 따르면서 오늘 매도분만 추가 활용
- 단점: `nrcvb_buy_amt` 자체가 보수적이라 T+1/T+2 정산 예정분이 누락될 수 있음
- 현재 `fetch_buy_orderable_cash()`는 이 방식이 아니라 KIS의 `nrcvb_buy_amt`(IRP는 `max_buy_amt`)를 그대로 반환한다.

### 접근 C — 적극적 (T+2 예수금 기준)
```
available = prvs_rcdl_excc_amt
```
- 의미: "모든 미정산 거래가 청산된 T+2 시점에 보유하게 될 예수금"
- 장점: 이전·오늘의 매도 대금이 모두 반영 → 운용 효율 최대
- 단점: CMA 등 즉시 주문 불가 금액이 포함될 가능성, 미수 정책 미반영 가능성
- 현재 `fetch_domestic_cash_balance()`와 플래너의 CASH 행은 이 기준을 사용한다.

> **선택 가이드**: T+2 결제 주기 안에서 **현금을 출금하지 않고 다른 종목 매수에만 쓰는** 시나리오라면 접근 C가 가장 자연스럽다. 다만 CMA 잔액이 별도로 존재하거나 미수 위험이 있는 계좌에서는 C가 과대 추정될 수 있으므로, 실거래 1회로 `dnca_tot_amt / nrcvb_buy_amt / prvs_rcdl_excc_amt`를 모두 로깅해 비교한 뒤 도입하는 것이 안전하다.

---

## 7. 현재 코드 사용 현황 (스냅샷)

| 위치 | 용도 | 사용 필드 | 비고 |
|---|---|---|---|
| `src/kis/client.py` `fetch_domestic_cash_balance()` | 플래너 입력 / 거래 전후 로깅 | `prvs_rcdl_excc_amt` | 접근 C |
| `src/kis/client.py` `fetch_domestic_total_balance()` | 플래너에 넘기는 잔고 DataFrame | 위 함수 결과를 CASH 행으로 추가 | 접근 C 기반 |
| `src/kis/client.py` `fetch_domestic_enable_buy()` | 종목별 주문 가능 조회 | `nrcvb_buy_amt`, `max_buy_amt`, `psbl_qty_calc_unpr` | inquire-psbl-order |
| `src/kis/client.py` `fetch_buy_orderable_cash()` | 매수 시작 시 가용 현금 | ISA/PPA는 `nrcvb_buy_amt`, IRP는 `max_buy_amt` | KIS 앱 "주문가능"과 일치시키려는 목적 |
| `src/executor.py` | 거래 전후 로그 | `fetch_domestic_cash_balance()` | 접근 C |
| `src/executor.py` | 매수 잔여 현금 추적 시작값 | `fetch_buy_orderable_cash()` | KIS 매수가능조회 기반 |
| `src/planner.py` | 총평가금액 계산 | `fetch_domestic_total_balance()` | 접근 C 기반 |

---

## 8. 자주 헷갈리는 점

- **`dnca_tot_amt`는 "총" 예수금이지만 미래 정산은 포함하지 않는다.** "총금액"이라는 이름에 속지 말 것. T+0 시점의 결제 완료분만 가리킨다.
- **`nxdy_excc_amt`, `prvs_rcdl_excc_amt`는 가산액이 아니라 잔액이다.** D+1 예수금에서 D+0 예수금을 빼면 "내일까지 들어올 순증액"을 얻을 수 있다.
- **`thdt_sll_amt`는 T+2 미반영분이다.** `dnca_tot_amt`에 이미 포함되어 있지 않음. 단순 합산해도 이중 계산이 아니다.
- **`nrcvb_buy_amt`는 종목 단위 보수적 추정이다.** 잔고조회의 `prvs_rcdl_excc_amt`와 일치하지 않는 것이 정상. 보통 더 작다.
- **CMA 잔액**은 `dnca_tot_amt`에 섞여 들어올 수 있고, 즉시 주문에는 못 쓸 수 있다. 그래서 현 코드는 `dnca_tot_amt`를 매수 기준으로 쓰지 않는다.
- **PPA(연금저축, postfix `22`)는 ISA와 동일한 일반 위탁 엔드포인트를 사용한다.** IRP(postfix `29`)만 pension 엔드포인트를 사용한다. 이 분기를 바꾸면 PPA 잔고/매수가능조회가 비정상 응답으로 돌아올 수 있다.

---

## 9. 참고 문서

- `kis_api_docs/md/주식잔고조회[v1_국내주식-006].md` — 잔고조회 전체 필드
- `kis_api_docs/md/퇴직연금 매수가능조회[v1_국내주식-034].md` — 매수가능조회 응답 예시
- `kis_api_docs/md/퇴직연금 잔고조회[v1_국내주식-036].md` — 퇴직연금 잔고 필드
- `kis_api_docs/md/퇴직연금 예수금조회[v1_국내주식-035].md` — 퇴직연금 예수금 전용 (`dnca_tota`, `nx2_day_sttl_amt` 등)
- `kis_api_docs/md/한투_API_목록.md` — 전체 엔드포인트 색인
