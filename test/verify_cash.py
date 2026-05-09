"""실거래 없이 KIS API로 예수금 산정을 검증하는 진단 스크립트.

비거래일에도 잔고조회는 정상 응답하므로 이 스크립트로 D+0/D+1/D+2 예수금을
실시간 확인할 수 있다. 거래는 일절 발생하지 않는다.

실행:
    uv run python -m test.verify_cash --account_type ISA
    uv run python -m test.verify_cash --account_type PPA
    uv run python -m test.verify_cash --account_type COMMON

확인 포인트:
- prvs_rcdl_excc_amt(D+2 예수금)이 사용자가 KIS 앱에서 보는 "T+2 기준 예수금"과 일치하는가
- fetch_domestic_cash_balance() 반환값이 prvs_rcdl_excc_amt와 동일한가
- fetch_domestic_total_balance()의 CASH 행이 prvs_rcdl_excc_amt 기반으로 산정되는가
"""
import sys
import argparse
from pathlib import Path

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

from src.kis.client import KISClient


def _fmt(n: int) -> str:
    return f'{n:>14,}원'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--account_type', type=str, required=True,
                        help='ISA / PPA / IRP / COMMON 등 kis_api_auth.json의 계좌 키')
    args = parser.parse_args()

    print(f'\n=== KIS 예수금 진단: account_type={args.account_type} ===\n')

    client = KISClient(args.account_type)

    breakdown = client.fetch_domestic_cash_breakdown()
    print('[1] fetch_domestic_cash_breakdown — 잔고조회 output2 원본 필드')
    print(f'    dnca_tot_amt        (D+0 예수금       ): {_fmt(breakdown["dnca_tot_amt"])}')
    print(f'    nxdy_excc_amt       (D+1 예수금       ): {_fmt(breakdown["nxdy_excc_amt"])}')
    print(f'    prvs_rcdl_excc_amt  (D+2 예수금 ★매수 기준): {_fmt(breakdown["prvs_rcdl_excc_amt"])}')
    print(f'    thdt_buy_amt        (금일 매수 체결액 ): {_fmt(breakdown["thdt_buy_amt"])}')
    print(f'    thdt_sll_amt        (금일 매도 체결액 ): {_fmt(breakdown["thdt_sll_amt"])}')
    print(f'    scts_evlu_amt       (유가증권 평가금액): {_fmt(breakdown["scts_evlu_amt"])}')
    print(f'    tot_evlu_amt        (총평가금액 ★KIS공식): {_fmt(breakdown["tot_evlu_amt"])}')

    delta_d1 = breakdown['nxdy_excc_amt'] - breakdown['dnca_tot_amt']
    delta_d2 = breakdown['prvs_rcdl_excc_amt'] - breakdown['nxdy_excc_amt']
    print(f'\n    D+1 정산 예정 순증: {_fmt(delta_d1)}')
    print(f'    D+2 정산 예정 순증: {_fmt(delta_d2)}')
    print(f'    D+0 → D+2 총 변화 : {_fmt(breakdown["prvs_rcdl_excc_amt"] - breakdown["dnca_tot_amt"])}')

    kis_identity_lhs = breakdown['scts_evlu_amt'] + breakdown['prvs_rcdl_excc_amt']
    kis_identity_rhs = breakdown['tot_evlu_amt']
    print(f'\n    KIS 항등식 검증: scts_evlu_amt + prvs_rcdl_excc_amt = {_fmt(kis_identity_lhs)}')
    print(f'                                      tot_evlu_amt = {_fmt(kis_identity_rhs)}')
    if kis_identity_lhs == kis_identity_rhs:
        print('    ✅ tot_evlu_amt = 유가증권 평가금액 + D+2 예수금 (KIS 공식 정의 일치)')
    else:
        diff = kis_identity_lhs - kis_identity_rhs
        print(f'    ⚠️  차이 {_fmt(diff)} — CMA·대출 등 다른 항목이 있을 수 있음')

    cash_balance = client.fetch_domestic_cash_balance()
    print(f'\n[2] fetch_domestic_cash_balance() (= prvs_rcdl_excc_amt 반환): {_fmt(cash_balance)}')
    assert cash_balance == breakdown['prvs_rcdl_excc_amt'], (
        f'❌ cash_balance({cash_balance:,})가 prvs_rcdl_excc_amt'
        f'({breakdown["prvs_rcdl_excc_amt"]:,})와 일치하지 않음'
    )
    print('    ✅ prvs_rcdl_excc_amt와 일치')

    orderable = client.fetch_buy_orderable_cash()
    print(f'\n[3] fetch_buy_orderable_cash() (= nrcvb_buy_amt, KIS 앱 "주문가능"): {_fmt(orderable)}')
    assert orderable <= cash_balance, (
        f'❌ 매수 한도({orderable:,})가 D+2 예수금({cash_balance:,})을 초과하면 안 됨'
    )
    print(f'    ✅ orderable({orderable:,}) ≤ cash_balance({cash_balance:,})')
    print(f'    ※ 차이 {cash_balance - orderable:,}원 = 수수료/세금 buffer 등 (KIS 비공개)')

    print('\n[4] fetch_domestic_total_balance() — 플래너에 들어가는 잔고 DataFrame')
    total_balance = client.fetch_domestic_total_balance()
    print(total_balance.to_string())

    cash_row = total_balance[total_balance['ticker'] == 'CASH']
    if not cash_row.empty:
        cash_value = int(cash_row['current_value'].iloc[0])
        print(f'\n    CASH 행 current_value: {_fmt(cash_value)}')
        assert cash_value == breakdown['prvs_rcdl_excc_amt'], (
            f'❌ CASH 행({cash_value:,})이 D+2 예수금({breakdown["prvs_rcdl_excc_amt"]:,})과 불일치'
        )
        print('    ✅ CASH 행이 D+2 예수금 기반으로 계상됨')

    stock_value = int(total_balance.loc[total_balance['ticker'] != 'CASH', 'current_value'].sum())
    total_value = int(total_balance['current_value'].sum())
    print(f'\n[5] 보유주식 평가금액 합계 (CASH 제외): {_fmt(stock_value)}')
    print(f'    KIS scts_evlu_amt                : {_fmt(breakdown["scts_evlu_amt"])}')
    if stock_value == breakdown['scts_evlu_amt']:
        print('    ✅ 우리 시스템의 보유주식 평가금액 = KIS scts_evlu_amt')
    else:
        diff = stock_value - breakdown['scts_evlu_amt']
        print(f'    ⚠️  차이 {_fmt(diff)} — 환산 방식 또는 페이지네이션 누락 확인 필요')

    print(f'\n[6] 총평가금액 (리밸런싱 기준 자산)')
    print(f'    우리 시스템 total_balance.sum(): {_fmt(total_value)}')
    print(f'    KIS tot_evlu_amt              : {_fmt(breakdown["tot_evlu_amt"])}')
    if total_value == breakdown['tot_evlu_amt']:
        print('    ✅ 거래 기준 총자산이 KIS tot_evlu_amt(= 유가증권 평가 + D+2 예수금)와 정확히 일치')
    else:
        diff = total_value - breakdown['tot_evlu_amt']
        print(f'    ⚠️  차이 {_fmt(diff)} — 보유주식 또는 현금 산정 검토 필요')

    # ------------------------------------------------------------------ #
    # [7] silent failure 탐지 (Codex P2 #2)                                 #
    # ------------------------------------------------------------------ #
    # 모든 ✅ 검증은 부등식·항등식 기반이라 0=0이면 trivially 통과한다.
    # 그러나 KIS가 빈 응답(msg_cd=KIOK0560 "조회할 내용이 없습니다")을 줄 때도
    # 동일한 패턴이라 잘못된 엔드포인트·파라미터가 silent하게 통과할 위험.
    # 본 케이스가 PPA postfix='22'에서 실제 발생 — 1.10c 진단으로 확인됨.
    print('\n[7] silent failure 탐지 (모든 값 0이거나 비정상 패턴 검사)')
    all_zero = (
        cash_balance == 0
        and orderable == 0
        and stock_value == 0
        and breakdown['tot_evlu_amt'] == 0
    )
    stocks_but_no_cash = (
        stock_value > 0 and cash_balance == 0 and orderable == 0
    )

    if all_zero:
        print('    ⚠️  WARNING: cash_balance·orderable·stock·tot_evlu_amt 모두 0원입니다.')
        print('       - 정말 빈 계좌라면 정상 (KIS 앱과 비교 후 확인).')
        print('       - 그러나 KIS 앱에 잔고가 표시된다면 silent failure 의심:')
        print('           · 잘못된 TR_ID/URL 엔드포인트 (예: PPA가 잘못된 pension API 호출)')
        print('           · KIS msg_cd="KIOK0560 조회할 내용이 없습니다"가 rt_cd=0 + 모든 필드 0으로 위장')
        print('           · ACNT_PRDT_CD 등 파라미터 오류로 빈 응답')
        print('       - raw 응답 점검: test/dump_ppa_orderable.py 형태의 진단 스크립트 권장.')
    elif stocks_but_no_cash:
        print('    ⚠️  WARNING: 보유주식 평가금액 > 0 이지만 cash_balance·orderable 모두 0원입니다.')
        print('       - 모든 자금을 주식에 투입한 상태 또는 매도 직후 결제 사이클이면 정상.')
        print('       - KIS 앱에 예수금이 표시된다면 silent failure 의심. raw 응답 점검 권장.')
    else:
        print('    ✅ 0=0 trivially 통과 패턴 아님 — silent failure 가능성 낮음.')

    print('\n=== 진단 완료. 위 D+2 값이 KIS 앱의 "예수금 (T+2)"과 일치하는지 확인하세요. ===\n')


if __name__ == '__main__':
    main()
