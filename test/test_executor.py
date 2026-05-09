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
    return OrderExecutor(kis_client, account_type='ISA', is_test=is_test)


# ---------------------------------------------------------------------------- #
# 세트 1 — _get_orderable_qty buy 분기 (산정 코어)                                  #
# ---------------------------------------------------------------------------- #

def test_orderable_qty_basic():
    """1-1. 기본 계산: cash=1,000,000원, calc=10,000원 → 99주 (0.99 버퍼 적용)."""
    ex = _make_executor()
    mock_response = {'psbl_qty_calc_unpr': '10000'}
    with patch.object(type(ex.kis_client), 'fetch_domestic_enable_buy', return_value=mock_response):
        qty = ex._get_orderable_qty(ticker='005930', transaction_type='buy', available_cash=1_000_000)
    assert qty == 99, f'기대 99주, 실제 {qty}주 (= floor(0.99 × 1,000,000 / 10,000))'
    print('✅ 1-1 _get_orderable_qty 기본: 1,000,000 / 10,000 → 99주 (0.99 버퍼)')


def test_orderable_qty_buffer_boundary():
    """1-2. 0.99 버퍼 경계: cash=100,100원, calc=10,000원 → 9주 (10주 X)."""
    ex = _make_executor()
    mock_response = {'psbl_qty_calc_unpr': '10000'}
    with patch.object(type(ex.kis_client), 'fetch_domestic_enable_buy', return_value=mock_response):
        qty = ex._get_orderable_qty(ticker='005930', transaction_type='buy', available_cash=100_100)
    # available_amt = 100,100 × 0.99 = 99,099 → 99,099 / 10,000 = 9.9099 → 9주
    assert qty == 9, f'기대 9주(10주는 0.99 버퍼 때문에 불가), 실제 {qty}주'
    print('✅ 1-2 _get_orderable_qty 버퍼 경계: 100,100 → 9주 (10주 차단)')


def test_orderable_qty_zero_calc_price():
    """1-3. calc_price=0 안전성: ZeroDivisionError 없이 0 반환."""
    ex = _make_executor()
    mock_response = {'psbl_qty_calc_unpr': '0'}
    with patch.object(type(ex.kis_client), 'fetch_domestic_enable_buy', return_value=mock_response):
        qty = ex._get_orderable_qty(ticker='005930', transaction_type='buy', available_cash=1_000_000)
    assert qty == 0, f'calc_price=0이면 0 반환해야 함, 실제 {qty}'
    print('✅ 1-3 _get_orderable_qty calc_price=0: ZeroDivision 없이 0 반환')


def test_orderable_qty_insufficient_cash():
    """1-4. 소액 현금: cash=5,000원, calc=10,000원 → 0주."""
    ex = _make_executor()
    mock_response = {'psbl_qty_calc_unpr': '10000'}
    with patch.object(type(ex.kis_client), 'fetch_domestic_enable_buy', return_value=mock_response):
        qty = ex._get_orderable_qty(ticker='005930', transaction_type='buy', available_cash=5_000)
    assert qty == 0, f'현금 부족 시 0주, 실제 {qty}주'
    print('✅ 1-4 _get_orderable_qty 현금 부족: 5,000 / 10,000 → 0주')


# ---------------------------------------------------------------------------- #
# 세트 2 — _execute_order의 min() 제한                                              #
# ---------------------------------------------------------------------------- #

def test_execute_order_required_exceeds_enable():
    """2-1. 계획수량 > 가능수량: required=100, enable=50 → transaction=50."""
    ex = _make_executor()
    plan_row = {
        'ticker': '005930', 'required_quantity': 100,
        'required_transaction': 'buy', 'current_price': 10000,
    }
    mock_order = MagicMock(return_value={'rt_cd': '0', 'msg1': 'ok'})
    with patch.object(ex, '_get_orderable_qty', return_value=50), \
         patch.object(type(ex.kis_client), 'create_domestic_order', mock_order):
        result = ex._execute_order(plan_row, order_index=0, available_cash=1_000_000)
    assert result['enable_quantity'] == 50
    assert result['transaction_quantity'] == 50, f'min(100,50)=50 기대, 실제 {result["transaction_quantity"]}'
    assert result['skipped_reason'] is None, f'정상 주문은 skipped_reason None, 실제 {result["skipped_reason"]}'
    assert result['is_success'] is True
    assert mock_order.call_count == 1, '체결 시도 1회 호출돼야 함'
    print('✅ 2-1 _execute_order min(): required=100 ∧ enable=50 → transaction=50')


def test_execute_order_zero_quantity_skips():
    """2-2. 가능수량=0: transaction=0, skipped_reason='zero_quantity', 주문 미호출."""
    ex = _make_executor()
    plan_row = {
        'ticker': '005930', 'required_quantity': 100,
        'required_transaction': 'buy', 'current_price': 10000,
    }
    mock_order = MagicMock()
    with patch.object(ex, '_get_orderable_qty', return_value=0), \
         patch.object(type(ex.kis_client), 'create_domestic_order', mock_order):
        result = ex._execute_order(plan_row, order_index=0, available_cash=1_000_000)
    assert result['transaction_quantity'] == 0
    assert result['skipped_reason'] == 'zero_quantity'
    assert result['is_success'] is None, '주문 미호출 시 is_success=None'
    assert result['response_msg'] is None
    assert mock_order.call_count == 0, '가능수량 0이면 create_domestic_order 호출 금지'
    print('✅ 2-2 _execute_order zero_quantity: skip + 주문 미호출')


# ---------------------------------------------------------------------------- #
# 세트 3 — is_test=True 경로                                                       #
# ---------------------------------------------------------------------------- #

def test_execute_order_test_mode_skips_real_order():
    """3-1. is_test=True: enable=50, required=50이어도 transaction=0, 주문 미호출."""
    ex = _make_executor(is_test=True)
    plan_row = {
        'ticker': '005930', 'required_quantity': 50,
        'required_transaction': 'buy', 'current_price': 10000,
    }
    mock_order = MagicMock()
    with patch.object(ex, '_get_orderable_qty', return_value=50), \
         patch.object(type(ex.kis_client), 'create_domestic_order', mock_order):
        result = ex._execute_order(plan_row, order_index=0, available_cash=1_000_000)
    assert result['enable_quantity'] == 50, 'enable_quantity는 그대로 50 보고'
    assert result['transaction_quantity'] == 0, 'is_test 모드에서는 transaction=0'
    assert result['skipped_reason'] == 'test_mode'
    assert mock_order.call_count == 0, 'is_test=True에서는 절대 create_domestic_order 호출 금지'
    print('✅ 3-1 is_test=True: 실주문 미호출 + skipped_reason=test_mode')


# ---------------------------------------------------------------------------- #
# 세트 4 — run_rebalancing 연속 매수 잔여 추적                                       #
# ---------------------------------------------------------------------------- #

_ORDER_RESULT_KEYS = [
    'ticker', 'enable_quantity', 'transaction_quantity',
    'skipped_reason', 'is_success', 'response_msg', 'transaction_order',
]


def _stub_order_result(ticker: str, transaction_quantity: int, transaction_order: int) -> dict:
    return {
        'ticker': ticker,
        'enable_quantity': transaction_quantity,
        'transaction_quantity': transaction_quantity,
        'skipped_reason': None,
        'is_success': True,
        'response_msg': 'ok',
        'transaction_order': transaction_order,
    }


def test_run_rebalancing_remaining_cash_decremented():
    """4-1. 두 종목 연속 매수: 첫 주문 100×5,000=500,000원 차감 후 두 번째 호출에 잔여 전달."""
    ex = _make_executor()
    plan_df = pd.DataFrame([
        {'ticker': '005930', 'required_quantity': 100, 'required_transaction': 'buy', 'current_price': 5000},
        {'ticker': '000660', 'required_quantity': 50,  'required_transaction': 'buy', 'current_price': 4000},
    ])
    with patch.object(type(ex.kis_client), 'fetch_buy_orderable_cash', return_value=1_000_000), \
         patch.object(type(ex.kis_client), 'fetch_domestic_cash_balance', return_value=1_000_000), \
         patch.object(ex, '_execute_order') as mock_exec:
        mock_exec.side_effect = [
            _stub_order_result('005930', 100, 0),
            _stub_order_result('000660', 50, 1),
        ]
        ex.run_rebalancing(plan_df)

    assert mock_exec.call_count == 2, f'매수 2건 = 2회 호출, 실제 {mock_exec.call_count}'
    # 첫 호출: available_cash = 1,000,000 (초기 nrcvb)
    assert mock_exec.call_args_list[0].kwargs['available_cash'] == 1_000_000, (
        f'첫 호출 available_cash=1,000,000 기대, 실제 {mock_exec.call_args_list[0].kwargs["available_cash"]:,}'
    )
    # 두 번째 호출: available_cash = 1,000,000 - 100×5,000 = 500,000
    assert mock_exec.call_args_list[1].kwargs['available_cash'] == 500_000, (
        f'두 번째 호출 available_cash=500,000 기대, 실제 {mock_exec.call_args_list[1].kwargs["available_cash"]:,}'
    )
    print('✅ 4-1 run_rebalancing 잔여 추적: 100×5,000=500,000 차감 후 두 번째 매수에 전달')


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
            _stub_order_result('005930', 0, 0),    # 체결 실패
            _stub_order_result('000660', 50, 1),
        ]
        ex.run_rebalancing(plan_df)

    assert mock_exec.call_args_list[0].kwargs['available_cash'] == 1_000_000
    # 첫 종목 transaction=0이면 차감 0 → 잔여 그대로
    assert mock_exec.call_args_list[1].kwargs['available_cash'] == 1_000_000, (
        f'체결 0이면 잔여 변동 없어야 함, 실제 {mock_exec.call_args_list[1].kwargs["available_cash"]:,}'
    )
    print('✅ 4-2 run_rebalancing 체결 실패 시 잔여 유지')


if __name__ == '__main__':
    test_orderable_qty_basic()
    test_orderable_qty_buffer_boundary()
    test_orderable_qty_zero_calc_price()
    test_orderable_qty_insufficient_cash()
    test_execute_order_required_exceeds_enable()
    test_execute_order_zero_quantity_skips()
    test_execute_order_test_mode_skips_real_order()
    test_run_rebalancing_remaining_cash_decremented()
    test_run_rebalancing_zero_fill_keeps_remaining_cash()
    print('\n전체 테스트 통과')
