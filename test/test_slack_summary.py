"""format_rebalancing_summary 단위 테스트 (ARCH-006).

검증:
- 전·후 분모 분리 (현금 명시 포함)
- 체결 기준이 filled_quantity (요청 수량 아님)
- 실패 주문은 후 비중에 영향 없음
- planner의 current_pct는 덮어쓰이지 않음
- filled_quantity 부재 시 transaction_quantity로 fallback (legacy 호환)
- buy-only / sell-only / no-cash 시나리오 정상 처리

실행: `uv run python -m test.test_slack_summary`
"""
import os
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

import pandas as pd

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))

# Slack 토큰 환경변수가 없어도 import는 통과해야 — slack_sdk WebClient는 lazy.
os.environ.setdefault('SLACK_BOT_TOKEN', 'xoxb-test')
os.environ.setdefault('SLACK_CHANNEL_ID', 'C-test')

from src.slack.client import format_rebalancing_summary  # noqa: E402


def _make_result_df(rows, cash_before: float):
    """planner.merge(trade_log) 형태의 result_df를 만든다.

    rows: list of dicts — 각 종목 (ticker, weight, current_price, current_quantity, current_value,
                            current_pct, required_transaction, required_quantity,
                            filled_quantity, transaction_quantity, is_success, ...).
    cash_before: 체결 전 예수금. CASH 행으로 추가.
    """
    df = pd.DataFrame(rows)
    cash_row = {
        'ticker': 'CASH',
        'stock_nm': 'WON_DEPOSIT',
        'weight': 0.0,
        'current_price': 0,
        'current_quantity': 0,
        'current_value': cash_before,
        'current_pct': 0.0,
        'required_transaction': None,
        'required_quantity': 0,
    }
    return pd.concat([df, pd.DataFrame([cash_row])], ignore_index=True)


# ---------------------------------------------------------------------------- #
# 세트 1 — 분모 분리 (현금 명시 포함)                                                #
# ---------------------------------------------------------------------------- #

def test_denominator_split_buy_and_sell():
    """1-1. buy + sell 동시 → 전 분모와 후 분모 모두 헤더에 표시."""
    rows = [
        # 005930: 사야 함 (filled 5주 × 100,000 = +500,000)
        {'ticker': '005930', 'stock_nm': '삼성전자', 'weight': 0.6, 'current_price': 100_000,
         'current_quantity': 0, 'current_value': 0, 'current_pct': 0.0,
         'required_transaction': 'buy', 'required_quantity': 5,
         'filled_quantity': 5, 'transaction_quantity': 5, 'is_success': True},
        # 035720: 팔아야 함 (filled 3주 × 100,000 = -300,000)
        {'ticker': '035720', 'stock_nm': '카카오', 'weight': 0.4, 'current_price': 100_000,
         'current_quantity': 8, 'current_value': 800_000, 'current_pct': 80.0,
         'required_transaction': 'sell', 'required_quantity': 3,
         'filled_quantity': 3, 'transaction_quantity': 3, 'is_success': True},
    ]
    # 전 예수금 200,000. 체결 후 잔여 = 200,000 + 300,000(매도) - 500,000(매수) = 0
    result_df = _make_result_df(rows, cash_before=200_000)
    summary = format_rebalancing_summary(
        result_df=result_df, remaining_cash=0,
        account_type='ISA', dt=datetime(2026, 5, 9, 18, 0),
    )
    # 전 총자산: 0 + 800,000 + 200,000 = 1,000,000
    # 후 총자산: 500,000 + 500,000 + 0     = 1,000,000
    assert '전 총자산: *1,000,000원*' in summary, f'분모 분리(전) 누락: {summary}'
    assert '후 총자산: *1,000,000원*' in summary, f'분모 분리(후) 누락: {summary}'
    assert '현금 200,000' in summary, '전 현금 표시 누락'
    assert '현금 0' in summary, '후 현금 표시 누락'
    print('✅ 1-1 buy+sell: 전·후 분모 분리 + 현금 명시 표시')


def test_denominator_split_buy_only():
    """1-2. buy-only (sell 없음): 전 현금 → 후 현금 감소, 분모 분리 일관."""
    rows = [
        {'ticker': '005930', 'stock_nm': '삼성', 'weight': 1.0, 'current_price': 100_000,
         'current_quantity': 0, 'current_value': 0, 'current_pct': 0.0,
         'required_transaction': 'buy', 'required_quantity': 5,
         'filled_quantity': 5, 'transaction_quantity': 5, 'is_success': True},
    ]
    result_df = _make_result_df(rows, cash_before=600_000)
    summary = format_rebalancing_summary(
        result_df=result_df, remaining_cash=100_000,
        account_type='ISA', dt=datetime(2026, 5, 9),
    )
    # 전: 0 + 600,000 = 600,000
    # 후: 500,000 + 100,000 = 600,000
    assert '전 총자산: *600,000원*' in summary
    assert '후 총자산: *600,000원*' in summary
    # 후 비중: 005930 = 500,000 / 600,000 ≈ 83.3%
    assert '후 83.3%' in summary, f'후 비중 계산 오류: {summary}'
    print('✅ 1-2 buy-only: 분모 분리 + 후 비중 일관')


def test_denominator_split_sell_only():
    """1-3. sell-only (buy 없음): 매도 대금 잔여 현금에 합산, 분모 일관."""
    rows = [
        {'ticker': '005930', 'stock_nm': '삼성', 'weight': 0.0, 'current_price': 100_000,
         'current_quantity': 5, 'current_value': 500_000, 'current_pct': 100.0,
         'required_transaction': 'sell', 'required_quantity': 5,
         'filled_quantity': 5, 'transaction_quantity': 5, 'is_success': True},
    ]
    result_df = _make_result_df(rows, cash_before=0)
    summary = format_rebalancing_summary(
        result_df=result_df, remaining_cash=500_000,
        account_type='ISA', dt=datetime(2026, 5, 9),
    )
    # 전: 500,000 + 0 = 500,000
    # 후: 0 + 500,000 = 500,000
    assert '전 총자산: *500,000원*' in summary
    assert '후 총자산: *500,000원*' in summary
    # 후 비중: 0 / 500,000 = 0%
    assert '후 0.0%' in summary
    print('✅ 1-3 sell-only: 분모 분리 + 매도 대금 잔여에 합산')


# ---------------------------------------------------------------------------- #
# 세트 2 — filled_quantity 기준 후 비중                                           #
# ---------------------------------------------------------------------------- #

def test_uses_filled_quantity_not_requested():
    """2-1. requested=10인데 filled=5만 → 후 평가금액은 filled 기준."""
    rows = [
        {'ticker': '005930', 'stock_nm': '삼성', 'weight': 1.0, 'current_price': 100_000,
         'current_quantity': 0, 'current_value': 0, 'current_pct': 0.0,
         'required_transaction': 'buy', 'required_quantity': 10,
         # 부분 체결 시뮬 (현실적으로는 KIS가 부분 체결을 잘 하진 않지만 격리 테스트용)
         'filled_quantity': 5, 'transaction_quantity': 5, 'is_success': True},
    ]
    result_df = _make_result_df(rows, cash_before=1_000_000)
    summary = format_rebalancing_summary(
        result_df=result_df, remaining_cash=500_000,
        account_type='ISA', dt=datetime(2026, 5, 9),
    )
    # 후 005930 평가금: 5 × 100,000 = 500,000 (10 × 100,000 = 1,000,000 아님)
    # 후 총자산: 500,000 + 500,000 = 1,000,000 → 후 비중 50%
    assert '후 50.0%' in summary, f'filled=5 반영 안 됨: {summary}'
    print('✅ 2-1 filled_quantity 기준: requested=10이어도 filled=5만큼만 반영')


def test_failed_order_zero_filled_no_after_change():
    """2-2. is_success=False / filled=0 → 후 비중에 영향 없음."""
    rows = [
        {'ticker': '005930', 'stock_nm': '삼성', 'weight': 0.5, 'current_price': 100_000,
         'current_quantity': 5, 'current_value': 500_000, 'current_pct': 50.0,
         'required_transaction': 'buy', 'required_quantity': 3,
         'filled_quantity': 0, 'transaction_quantity': 0, 'is_success': False},
        {'ticker': '035720', 'stock_nm': '카카오', 'weight': 0.5, 'current_price': 100_000,
         'current_quantity': 5, 'current_value': 500_000, 'current_pct': 50.0,
         'required_transaction': None, 'required_quantity': 0,
         'filled_quantity': 0, 'transaction_quantity': 0, 'is_success': None},
    ]
    result_df = _make_result_df(rows, cash_before=0)
    summary = format_rebalancing_summary(
        result_df=result_df, remaining_cash=0,
        account_type='ISA', dt=datetime(2026, 5, 9),
    )
    # 실패 주문이라 005930 후 평가금은 그대로 500,000
    # 전 = 후 = 1,000,000, 005930 비중 50%
    assert '전 50.0% (+0.0) → 후 50.0% (+0.0)' in summary, f'실패 주문 영향 받음: {summary}'
    print('✅ 2-2 실패 주문(filled=0): 후 비중 변화 없음')


def test_falls_back_to_transaction_quantity_if_filled_missing():
    """2-3. legacy result_df에 filled_quantity 컬럼이 없어도 transaction_quantity로 폴백."""
    rows = [
        {'ticker': '005930', 'stock_nm': '삼성', 'weight': 1.0, 'current_price': 100_000,
         'current_quantity': 0, 'current_value': 0, 'current_pct': 0.0,
         'required_transaction': 'buy', 'required_quantity': 5,
         # filled_quantity 컬럼 부재 — transaction_quantity만 있음
         'transaction_quantity': 5, 'is_success': True},
    ]
    result_df = _make_result_df(rows, cash_before=1_000_000)
    # filled_quantity 컬럼 자체를 제거
    assert 'filled_quantity' not in result_df.columns
    summary = format_rebalancing_summary(
        result_df=result_df, remaining_cash=500_000,
        account_type='ISA', dt=datetime(2026, 5, 9),
    )
    # transaction_quantity=5로 폴백되어 후 평가금 500,000 반영
    assert '후 총자산: *1,000,000원*' in summary
    assert '후 50.0%' in summary
    print('✅ 2-3 filled_quantity 부재 → transaction_quantity 폴백 (legacy 호환)')


# ---------------------------------------------------------------------------- #
# 세트 3 — planner의 current_pct 보존                                            #
# ---------------------------------------------------------------------------- #

def test_planner_current_pct_preserved():
    """3-1. format_rebalancing_summary는 result_df의 current_pct를 덮어쓰지 않는다."""
    rows = [
        {'ticker': '005930', 'stock_nm': '삼성', 'weight': 0.5, 'current_price': 100_000,
         'current_quantity': 5, 'current_value': 500_000,
         'current_pct': 42.0,  # planner가 만든 임의 값 — 덮어쓰지 않으면 그대로 유지
         'required_transaction': None, 'required_quantity': 0,
         'filled_quantity': 0, 'transaction_quantity': 0, 'is_success': None},
    ]
    result_df = _make_result_df(rows, cash_before=500_000)
    snapshot = result_df.copy()
    _ = format_rebalancing_summary(
        result_df=result_df, remaining_cash=500_000,
        account_type='ISA', dt=datetime(2026, 5, 9),
    )
    # 호출 후에도 result_df의 current_pct는 변경되지 않아야 함 (호출자 result_df 보호)
    assert (result_df['current_pct'] == snapshot['current_pct']).all(), \
        '호출자 DataFrame의 current_pct가 변경됨 — 덮어쓰기 발생'
    print('✅ 3-1 planner current_pct 보존: 호출자 DataFrame 변경 없음')


# ---------------------------------------------------------------------------- #
# Runner                                                                       #
# ---------------------------------------------------------------------------- #
if __name__ == '__main__':
    test_denominator_split_buy_and_sell()
    test_denominator_split_buy_only()
    test_denominator_split_sell_only()
    test_uses_filled_quantity_not_requested()
    test_failed_order_zero_filled_no_after_change()
    test_falls_back_to_transaction_quantity_if_filled_missing()
    test_planner_current_pct_preserved()
    print('\n🎉 모든 ARCH-006 Slack 비중 테스트 통과')
