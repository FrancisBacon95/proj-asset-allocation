"""KIS 예수금 산정 단위 테스트.

거래 없이 모킹된 응답으로 다음을 검증한다:
- fetch_domestic_cash_breakdown: 7개 필드를 정수로 정확히 추출하는가
- fetch_domestic_cash_balance: prvs_rcdl_excc_amt(D+2 예수금)를 반환하는가
- fetch_buy_orderable_cash: inquire-psbl-order의 nrcvb_buy_amt를 정수로 반환하는가

실행: `uv run python -m test.test_cash_breakdown`
"""
import sys
from pathlib import Path
from unittest.mock import patch

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))


SAMPLE_BALANCE_RESPONSE = {
    'output1': [],
    'output2': [{
        'dnca_tot_amt':       '490000',    # D+0
        'nxdy_excc_amt':      '700000',    # D+1
        'prvs_rcdl_excc_amt': '900000',    # D+2 ← 매수 기준
        'thdt_buy_amt':       '0',
        'thdt_sll_amt':       '0',
        'cma_evlu_amt':       '0',
        'scts_evlu_amt':      '5000000',   # 유가증권 평가금액
        'tot_evlu_amt':       '5900000',   # = scts_evlu_amt + prvs_rcdl_excc_amt
    }],
    'tr_cont': 'D',
}


def _make_client_skipping_init():
    """KISClient.__init__의 인증·토큰 흐름을 우회한 인스턴스 생성."""
    from src.kis.client import KISClient
    return KISClient.__new__(KISClient)


def test_fetch_domestic_cash_breakdown_returns_all_fields():
    client = _make_client_skipping_init()
    with patch.object(type(client), '_domestic_balance_page', return_value=SAMPLE_BALANCE_RESPONSE):
        breakdown = client.fetch_domestic_cash_breakdown()
    assert breakdown == {
        'dnca_tot_amt': 490_000,
        'nxdy_excc_amt': 700_000,
        'prvs_rcdl_excc_amt': 900_000,
        'thdt_buy_amt': 0,
        'thdt_sll_amt': 0,
        'scts_evlu_amt': 5_000_000,
        'tot_evlu_amt': 5_900_000,
    }, f'breakdown 결과가 예상과 다름: {breakdown}'
    assert breakdown['tot_evlu_amt'] == breakdown['scts_evlu_amt'] + breakdown['prvs_rcdl_excc_amt'], (
        'KIS 항등식 위반: tot_evlu_amt = scts_evlu_amt + prvs_rcdl_excc_amt'
    )
    print('✅ fetch_domestic_cash_breakdown: 7개 필드 정확히 추출 + KIS 항등식 일치')


def test_fetch_domestic_cash_balance_returns_d2():
    client = _make_client_skipping_init()
    with patch.object(type(client), '_domestic_balance_page', return_value=SAMPLE_BALANCE_RESPONSE):
        balance = client.fetch_domestic_cash_balance()
    assert balance == 900_000, f'fetch_domestic_cash_balance가 D+2(900,000)를 반환해야 하나 {balance:,}원 반환'
    print('✅ fetch_domestic_cash_balance: prvs_rcdl_excc_amt(D+2 예수금) 반환')


def test_fetch_buy_orderable_cash_returns_nrcvb():
    """fetch_buy_orderable_cash는 inquire-psbl-order의 nrcvb_buy_amt를 정수로 반환한다.

    실험으로 nrcvb_buy_amt가 종목 무관 계좌 단위 상수임이 확인됐다 (test/dump_isa_orderable.py).
    cash_balance(D+2)와는 의도적으로 다른 값을 반환할 수 있다.
    """
    sample_psbl_response = {
        'ord_psbl_cash':       '327867',
        'ord_psbl_sbst':       '52210610',
        'ruse_psbl_amt':       '189501',
        'fund_rpch_chgs':      '0',
        'psbl_qty_calc_unpr':  '349000',
        'nrcvb_buy_amt':       '510632',   # ← 본 함수가 반환해야 할 값
        'nrcvb_buy_qty':       '1',
        'max_buy_amt':         '510632',
        'max_buy_qty':         '1',
        'cma_evlu_amt':        '0',
    }
    client = _make_client_skipping_init()
    with patch.object(type(client), 'fetch_domestic_enable_buy', return_value=sample_psbl_response):
        orderable = client.fetch_buy_orderable_cash()
    assert orderable == 510_632, (
        f'fetch_buy_orderable_cash는 nrcvb_buy_amt(510,632)를 반환해야 함, 실제 {orderable:,}원'
    )
    assert isinstance(orderable, int), f'반환 타입은 int여야 함, 실제 {type(orderable).__name__}'
    print('✅ fetch_buy_orderable_cash: nrcvb_buy_amt를 int로 정확히 반환')


def test_buy_orderable_cash_independent_of_balance():
    """fetch_buy_orderable_cash와 fetch_domestic_cash_balance는 서로 다른 API를 호출하며,
    값이 달라도 정상이다 (D+2 예수금 vs 미수없는 매수 한도)."""
    sample_psbl_response = {
        'psbl_qty_calc_unpr': '349000',
        'nrcvb_buy_amt':      '510632',
        'max_buy_amt':        '510632',
        'max_buy_qty':        '1',
    }
    client = _make_client_skipping_init()
    with patch.object(type(client), '_domestic_balance_page', return_value=SAMPLE_BALANCE_RESPONSE), \
         patch.object(type(client), 'fetch_domestic_enable_buy', return_value=sample_psbl_response):
        balance = client.fetch_domestic_cash_balance()
        orderable = client.fetch_buy_orderable_cash()
    assert balance == 900_000, f'cash_balance는 D+2(900,000) 반환, 실제 {balance:,}'
    assert orderable == 510_632, f'orderable은 nrcvb(510,632) 반환, 실제 {orderable:,}'
    assert balance != orderable, 'D+2와 nrcvb가 서로 다른 값을 반환할 수 있어야 함'
    print('✅ cash_balance(D+2)와 orderable(nrcvb)는 독립적 — 다른 값 반환 가능')


def test_today_with_sells_and_buys_uses_d2_only():
    """오늘 매도/매수가 있어도 D+2 예수금을 그대로 반환 (이중 가산 없음)."""
    response = {
        'output1': [],
        'output2': [{
            'dnca_tot_amt':       '490000',
            'nxdy_excc_amt':      '600000',
            'prvs_rcdl_excc_amt': '950000',   # API가 이미 매도/매수 반영해 산정
            'thdt_buy_amt':       '50000',
            'thdt_sll_amt':       '500000',
            'scts_evlu_amt':      '5000000',
            'tot_evlu_amt':       '5950000',
        }],
        'tr_cont': 'D',
    }
    client = _make_client_skipping_init()
    with patch.object(type(client), '_domestic_balance_page', return_value=response):
        balance = client.fetch_domestic_cash_balance()
    assert balance == 950_000, f'D+2 단독값이 반환돼야 함, 실제 {balance:,}'
    print('✅ 오늘 매수/매도 발생 케이스: D+2 단독 반환 (이중 가산 없음)')


if __name__ == '__main__':
    test_fetch_domestic_cash_breakdown_returns_all_fields()
    test_fetch_domestic_cash_balance_returns_d2()
    test_fetch_buy_orderable_cash_returns_nrcvb()
    test_buy_orderable_cash_independent_of_balance()
    test_today_with_sells_and_buys_uses_d2_only()
    print('\n전체 테스트 통과')
