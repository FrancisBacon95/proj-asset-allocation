"""OrderExecutor 매수 산정 로직 단위 테스트.

거래 없이 모킹된 KIS 응답으로 다음을 검증한다:
- _get_orderable_qty buy 분기: 0.99 버퍼·정수 라운딩·calc_price=0 안전성
- _execute_order: 계획수량과 가능수량의 min 제한, zero_quantity skip
- is_test=True 경로: 실제 주문 미호출
- run_rebalancing: 연속 매수 시 잔여 현금 차감 추적

실행: `uv run python -m test.test_executor`
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))

from src.executor import OrderExecutor
from src.kis.client import KISClient


def _make_executor(is_test: bool = False) -> OrderExecutor:
    """KISClient.__init__의 인증·토큰 흐름을 우회한 OrderExecutor 인스턴스 생성."""
    kis_client = KISClient.__new__(KISClient)  # __init__ 스킵
    kis_client.acc_no_postfix = '01'
    kis_client.acc_no_prefix = '00000000'
    kis_client.mock = False
    return OrderExecutor(kis_client, account_type='ISA', is_test=is_test)


# ---------------------------------------------------------------------------- #
# 세트 1 — _get_orderable_qty buy 분기 (산정 코어)                                  #
# ---------------------------------------------------------------------------- #

def test_orderable_qty_basic():
    """1-1. 기본 계산: cash=1,000,000원, calc=10,000원 → (99주, 10000) 튜플 반환."""
    ex = _make_executor()
    mock_response = {'psbl_qty_calc_unpr': '10000'}
    with patch.object(type(ex.kis_client), 'fetch_domestic_enable_buy', return_value=mock_response):
        qty, calc_price = ex._get_orderable_qty(ticker='005930', transaction_type='buy', available_cash=1_000_000)
    assert qty == 99, f'기대 99주, 실제 {qty}주 (= floor(0.99 × 1,000,000 / 10,000))'
    assert calc_price == 10_000, f'calc_price 노출 검증, 실제 {calc_price}'
    print('✅ 1-1 _get_orderable_qty 기본: (99, 10000) 튜플 반환')


def test_orderable_qty_buffer_boundary():
    """1-2. 0.99 버퍼 경계: cash=100,100원, calc=10,000원 → (9주, 10000)."""
    ex = _make_executor()
    mock_response = {'psbl_qty_calc_unpr': '10000'}
    with patch.object(type(ex.kis_client), 'fetch_domestic_enable_buy', return_value=mock_response):
        qty, calc_price = ex._get_orderable_qty(ticker='005930', transaction_type='buy', available_cash=100_100)
    # available_amt = 100,100 × 0.99 = 99,099 → 99,099 / 10,000 = 9.9099 → 9주
    assert qty == 9, f'기대 9주(10주는 0.99 버퍼 때문에 불가), 실제 {qty}주'
    assert calc_price == 10_000
    print('✅ 1-2 _get_orderable_qty 버퍼 경계: (9, 10000)')


def test_orderable_qty_zero_calc_price():
    """1-3. calc_price=0 안전성: ZeroDivisionError 없이 (0, 0) 반환."""
    ex = _make_executor()
    mock_response = {'psbl_qty_calc_unpr': '0'}
    with patch.object(type(ex.kis_client), 'fetch_domestic_enable_buy', return_value=mock_response):
        qty, calc_price = ex._get_orderable_qty(ticker='005930', transaction_type='buy', available_cash=1_000_000)
    assert qty == 0, f'calc_price=0이면 0 반환해야 함, 실제 {qty}'
    assert calc_price == 0
    print('✅ 1-3 _get_orderable_qty calc_price=0: ZeroDivision 없이 (0, 0) 반환')


def test_orderable_qty_insufficient_cash():
    """1-4. 소액 현금: cash=5,000원, calc=10,000원 → (0주, 10000)."""
    ex = _make_executor()
    mock_response = {'psbl_qty_calc_unpr': '10000'}
    with patch.object(type(ex.kis_client), 'fetch_domestic_enable_buy', return_value=mock_response):
        qty, calc_price = ex._get_orderable_qty(ticker='005930', transaction_type='buy', available_cash=5_000)
    assert qty == 0, f'현금 부족 시 0주, 실제 {qty}주'
    assert calc_price == 10_000
    print('✅ 1-4 _get_orderable_qty 현금 부족: (0, 10000)')


# ---------------------------------------------------------------------------- #
# 세트 2 — _execute_order의 min() 제한                                              #
# ---------------------------------------------------------------------------- #

def test_execute_order_required_exceeds_enable():
    """2-1. 계획수량 > 가능수량: required=100, enable=50 → requested=50, filled=50."""
    ex = _make_executor()
    plan_row = {
        'ticker': '005930', 'required_quantity': 100,
        'required_transaction': 'buy', 'current_price': 10000,
    }
    mock_order = MagicMock(return_value={'rt_cd': '0', 'msg1': 'ok'})
    with patch.object(ex, '_get_orderable_qty', return_value=(50, 10_000)), \
         patch.object(type(ex.kis_client), 'create_domestic_order', mock_order):
        result = ex._execute_order(plan_row, order_index=0, available_cash=1_000_000)
    assert result['enable_quantity'] == 50
    # ARCH-004: requested = KIS에 요청한 수량, filled = 성공 체결 수량
    assert result['requested_quantity'] == 50, f'requested=min(100,50)=50, 실제 {result["requested_quantity"]}'
    assert result['filled_quantity'] == 50, f'성공이면 filled=requested, 실제 {result["filled_quantity"]}'
    # transaction_quantity (deprecated alias) = filled
    assert result['transaction_quantity'] == 50
    assert result['calc_price'] == 10_000
    assert result['skipped_reason'] is None
    assert result['is_success'] is True
    assert mock_order.call_count == 1, '체결 시도 1회 호출돼야 함'
    print('✅ 2-1 _execute_order min(): requested=50, filled=50, transaction_quantity=50(alias)')


def test_execute_order_zero_quantity_skips():
    """2-2. 가능수량=0: requested=0, filled=0, skipped_reason='zero_quantity', 주문 미호출."""
    ex = _make_executor()
    plan_row = {
        'ticker': '005930', 'required_quantity': 100,
        'required_transaction': 'buy', 'current_price': 10000,
    }
    mock_order = MagicMock()
    with patch.object(ex, '_get_orderable_qty', return_value=(0, 10_000)), \
         patch.object(type(ex.kis_client), 'create_domestic_order', mock_order):
        result = ex._execute_order(plan_row, order_index=0, available_cash=1_000_000)
    assert result['requested_quantity'] == 0
    assert result['filled_quantity'] == 0
    assert result['transaction_quantity'] == 0  # alias
    assert result['skipped_reason'] == 'zero_quantity'
    assert result['calc_price'] == 10_000
    assert result['is_success'] is None
    assert result['response_msg'] is None
    assert mock_order.call_count == 0
    print('✅ 2-2 _execute_order zero_quantity: requested=0, filled=0, 주문 미호출')


def test_execute_order_failed_order_filled_zero():
    """2-3 신규 (ARCH-004 + ARCH-008): KIS 응답 rt_cd != '0'일 때 requested>0, filled=0."""
    ex = _make_executor()
    plan_row = {
        'ticker': '005930', 'required_quantity': 100,
        'required_transaction': 'buy', 'current_price': 10000,
    }
    # KIS가 주문 거절: rt_cd='1' (실패)
    mock_order = MagicMock(return_value={'rt_cd': '1', 'msg1': 'rejected'})
    with patch.object(ex, '_get_orderable_qty', return_value=(50, 10_000)), \
         patch.object(type(ex.kis_client), 'create_domestic_order', mock_order):
        result = ex._execute_order(plan_row, order_index=0, available_cash=1_000_000)
    # 우리는 50주를 요청했지만 KIS가 거절 → filled=0
    assert result['requested_quantity'] == 50, '요청 수량은 보존 (감사 추적)'
    assert result['filled_quantity'] == 0, '실패 주문은 filled=0 (ARCH-008 — 잔여 차감 안 됨)'
    assert result['transaction_quantity'] == 0, 'alias도 filled=0'
    assert result['is_success'] is False, 'rt_cd != 0 → is_success=False'
    assert result['response_msg'] == 'rejected'
    assert mock_order.call_count == 1, '주문 시도는 했음 (실패할 뿐)'
    print('✅ 2-3 ARCH-004/008: 실패 주문 → requested=50, filled=0 (잔여 차감 보호)')


# ---------------------------------------------------------------------------- #
# 세트 3 — is_test=True 경로                                                       #
# ---------------------------------------------------------------------------- #

def test_execute_order_test_mode_skips_real_order():
    """3-1. is_test=True: enable=50, required=50이어도 requested=0, filled=0 (실 KIS 호출 자체 안 함)."""
    ex = _make_executor(is_test=True)
    plan_row = {
        'ticker': '005930', 'required_quantity': 50,
        'required_transaction': 'buy', 'current_price': 10000,
    }
    mock_order = MagicMock()
    with patch.object(ex, '_get_orderable_qty', return_value=(50, 10_000)), \
         patch.object(type(ex.kis_client), 'create_domestic_order', mock_order):
        result = ex._execute_order(plan_row, order_index=0, available_cash=1_000_000)
    assert result['enable_quantity'] == 50
    # is_test에서는 KIS에 요청 자체를 안 했으므로 requested=0
    assert result['requested_quantity'] == 0, 'is_test에서는 KIS에 요청 안 함 → requested=0'
    assert result['filled_quantity'] == 0
    assert result['transaction_quantity'] == 0  # alias
    assert result['skipped_reason'] == 'test_mode'
    assert result['calc_price'] == 10_000
    assert mock_order.call_count == 0, 'is_test=True에서는 절대 create_domestic_order 호출 금지'
    print('✅ 3-1 is_test=True: requested=0, filled=0, 실주문 미호출')


# ---------------------------------------------------------------------------- #
# 세트 4 — run_rebalancing 연속 매수 잔여 추적                                       #
# ---------------------------------------------------------------------------- #

_ORDER_RESULT_KEYS = [
    'ticker', 'enable_quantity',
    'requested_quantity', 'filled_quantity', 'transaction_quantity',  # ARCH-004
    'calc_price', 'skipped_reason', 'is_success', 'response_msg', 'transaction_order',
]


def _stub_order_result(ticker: str, filled_quantity: int, transaction_order: int,
                       calc_price: int = None, requested_quantity: int = None) -> dict:
    """기본 stub: 성공 케이스(filled=requested). requested_quantity 명시하면 분리 가능."""
    requested = requested_quantity if requested_quantity is not None else filled_quantity
    return {
        'ticker': ticker,
        'enable_quantity': max(filled_quantity, requested),
        'requested_quantity': requested,
        'filled_quantity': filled_quantity,
        'transaction_quantity': filled_quantity,  # deprecated alias = filled
        'calc_price': calc_price,
        'skipped_reason': None,
        'is_success': filled_quantity > 0,
        'response_msg': 'ok' if filled_quantity > 0 else None,
        'transaction_order': transaction_order,
    }


def test_run_rebalancing_remaining_cash_decremented():
    """4-1. 두 종목 연속 매수: 첫 주문 100×5,500(calc_price)=550,000원 차감 후 두 번째 호출에 잔여 전달."""
    ex = _make_executor()
    plan_df = pd.DataFrame([
        {'ticker': '005930', 'required_quantity': 100, 'required_transaction': 'buy', 'current_price': 5000},
        {'ticker': '000660', 'required_quantity': 50,  'required_transaction': 'buy', 'current_price': 4000},
    ])
    with patch.object(type(ex.kis_client), 'fetch_buy_orderable_cash', return_value=1_000_000), \
         patch.object(type(ex.kis_client), 'fetch_domestic_cash_balance', return_value=1_000_000), \
         patch.object(ex, '_execute_order') as mock_exec:
        # current_price=5000, calc_price=5500 (보수단가가 더 높음)
        mock_exec.side_effect = [
            _stub_order_result('005930', 100, 0, calc_price=5500),
            _stub_order_result('000660', 50, 1, calc_price=4500),
        ]
        ex.run_rebalancing(plan_df)

    assert mock_exec.call_count == 2, f'매수 2건 = 2회 호출, 실제 {mock_exec.call_count}'
    # 첫 호출: available_cash = 1,000,000 (초기 nrcvb)
    assert mock_exec.call_args_list[0].kwargs['available_cash'] == 1_000_000, (
        f'첫 호출 available_cash=1,000,000 기대, 실제 {mock_exec.call_args_list[0].kwargs["available_cash"]:,}'
    )
    # 두 번째 호출: available_cash = 1,000,000 - 100×5,500 = 450,000 (calc_price 기준 보수 차감)
    assert mock_exec.call_args_list[1].kwargs['available_cash'] == 450_000, (
        f'두 번째 호출 available_cash=450,000(=1,000,000-100×5500) 기대, '
        f'실제 {mock_exec.call_args_list[1].kwargs["available_cash"]:,}'
    )
    print('✅ 4-1 run_rebalancing 잔여 추적: 100×5,500=550,000 차감 후 두 번째 매수에 전달')


def test_run_rebalancing_zero_fill_keeps_remaining_cash():
    """4-2. 첫 종목 체결 실패(transaction_quantity=0): 잔여 그대로 두 번째 매수에 전달."""
    ex = _make_executor()
    plan_df = pd.DataFrame([
        {'ticker': '005930', 'required_quantity': 100, 'required_transaction': 'buy', 'current_price': 5000},
        {'ticker': '000660', 'required_quantity': 50,  'required_transaction': 'buy', 'current_price': 4000},
    ])
    with patch.object(type(ex.kis_client), 'fetch_buy_orderable_cash', return_value=1_000_000), \
         patch.object(type(ex.kis_client), 'fetch_domestic_cash_balance', return_value=1_000_000), \
         patch.object(ex, '_execute_order') as mock_exec:
        mock_exec.side_effect = [
            _stub_order_result('005930', 0, 0, calc_price=5500),    # 체결 실패 (calc_price 있어도 qty=0이면 차감 0)
            _stub_order_result('000660', 50, 1, calc_price=4500),
        ]
        ex.run_rebalancing(plan_df)

    assert mock_exec.call_args_list[0].kwargs['available_cash'] == 1_000_000
    # 첫 종목 transaction=0이면 차감 0 → 잔여 그대로
    assert mock_exec.call_args_list[1].kwargs['available_cash'] == 1_000_000, (
        f'체결 0이면 잔여 변동 없어야 함, 실제 {mock_exec.call_args_list[1].kwargs["available_cash"]:,}'
    )
    print('✅ 4-2 run_rebalancing 체결 실패 시 잔여 유지')


def test_run_rebalancing_uses_calc_price_for_deduction():
    """4-3. 매수 잔여 차감이 current_price가 아닌 calc_price(보수단가) 기준으로 일어남을 검증.

    calc_price > current_price일 때 우리 추적 잔여가 더 작아져야 한다 (보수적).
    current_price 기준이라면 두 번째 호출 available_cash=950,000이 되겠지만,
    calc_price 기준이므로 945,000이 되어야 한다.
    """
    ex = _make_executor()
    plan_df = pd.DataFrame([
        {'ticker': '005930', 'required_quantity': 10, 'required_transaction': 'buy', 'current_price': 5000},
        {'ticker': '000660', 'required_quantity': 5,  'required_transaction': 'buy', 'current_price': 4000},
    ])
    with patch.object(type(ex.kis_client), 'fetch_buy_orderable_cash', return_value=1_000_000), \
         patch.object(type(ex.kis_client), 'fetch_domestic_cash_balance', return_value=1_000_000), \
         patch.object(ex, '_execute_order') as mock_exec:
        # current_price=5000이지만 calc_price=5500 (보수단가가 더 높음)
        mock_exec.side_effect = [
            _stub_order_result('005930', 10, 0, calc_price=5500),
            _stub_order_result('000660', 5, 1, calc_price=4500),
        ]
        ex.run_rebalancing(plan_df)
    # 첫 종목 차감 = 10 × 5500 = 55,000 (current_price 기준이면 50,000)
    # 두 번째 매수 시 available_cash = 1,000,000 - 55,000 = 945,000
    assert mock_exec.call_args_list[1].kwargs['available_cash'] == 945_000, (
        f'calc_price=5500 기준 차감이어야 함 (945,000), 실제 {mock_exec.call_args_list[1].kwargs["available_cash"]:,}. '
        f'현재가 기준이면 950,000이 됐을 것.'
    )
    print('✅ 4-3 run_rebalancing: 잔여 차감이 calc_price 기준 (보수적)')


def test_run_rebalancing_failed_order_no_deduction():
    """4-4 신규 (ARCH-008): KIS 주문 실패(rt_cd!='0')는 filled=0이므로 잔여 차감 안 됨.

    requested=50이지만 filled=0인 stub → 차감 0 → 두 번째 매수 시 잔여 그대로.
    """
    ex = _make_executor()
    plan_df = pd.DataFrame([
        {'ticker': '005930', 'required_quantity': 100, 'required_transaction': 'buy', 'current_price': 5000},
        {'ticker': '000660', 'required_quantity': 50,  'required_transaction': 'buy', 'current_price': 4000},
    ])
    with patch.object(type(ex.kis_client), 'fetch_buy_orderable_cash', return_value=1_000_000), \
         patch.object(type(ex.kis_client), 'fetch_domestic_cash_balance', return_value=1_000_000), \
         patch.object(ex, '_execute_order') as mock_exec:
        # 첫 주문: requested=50, filled=0 (KIS 거절)
        failed_stub = {
            'ticker': '005930', 'enable_quantity': 50,
            'requested_quantity': 50, 'filled_quantity': 0, 'transaction_quantity': 0,
            'calc_price': 5500, 'skipped_reason': None, 'is_success': False,
            'response_msg': 'rejected', 'transaction_order': 0,
        }
        mock_exec.side_effect = [
            failed_stub,
            _stub_order_result('000660', 50, 1, calc_price=4500),
        ]
        ex.run_rebalancing(plan_df)

    assert mock_exec.call_args_list[0].kwargs['available_cash'] == 1_000_000
    # 첫 주문 filled=0이므로 잔여 차감 0 → 두 번째도 1,000,000 그대로
    assert mock_exec.call_args_list[1].kwargs['available_cash'] == 1_000_000, (
        f'KIS 거절 주문(filled=0)은 차감 안 됨. 실제 {mock_exec.call_args_list[1].kwargs["available_cash"]:,}'
    )
    print('✅ 4-4 ARCH-008: 실패 주문(filled=0)은 잔여 차감 안 됨 (요청 수량 보존)')


if __name__ == '__main__':
    test_orderable_qty_basic()
    test_orderable_qty_buffer_boundary()
    test_orderable_qty_zero_calc_price()
    test_orderable_qty_insufficient_cash()
    test_execute_order_required_exceeds_enable()
    test_execute_order_zero_quantity_skips()
    test_execute_order_failed_order_filled_zero()
    test_execute_order_test_mode_skips_real_order()
    test_run_rebalancing_remaining_cash_decremented()
    test_run_rebalancing_zero_fill_keeps_remaining_cash()
    test_run_rebalancing_uses_calc_price_for_deduction()
    test_run_rebalancing_failed_order_no_deduction()
    print('\n전체 테스트 통과')
