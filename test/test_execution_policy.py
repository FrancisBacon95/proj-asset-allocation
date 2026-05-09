"""ExecutionPolicy + Planner 주입 단위 테스트 (ARCH-007).

거래 없이 모킹된 KIS 응답으로 다음을 검증한다:
- ExecutionPolicy 기본값 = 종전 모듈 상수 (회귀 보장)
- ExecutionPolicy 검증: 음수 buffer_cash, 비정상 비율 등은 ValueError
- PortfolioPlanner: policy 미주입 → DEFAULT_EXECUTION_POLICY 사용
- PortfolioPlanner: 커스텀 policy 주입 → buffer_cash가 target_value 산정에 반영됨

(executor.py 측 정책 주입은 ARCH-004/008 머지 후 별도 패치)

실행: `uv run python -m test.test_execution_policy`
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))

from src.policy import DEFAULT_EXECUTION_POLICY, ExecutionPolicy
from src.planner import PortfolioPlanner, BUFFER_CASH
from src.executor import OrderExecutor, SELL_TO_BUY_WAIT_SECONDS


# ---------------------------------------------------------------------------- #
# 세트 1 — ExecutionPolicy 기본값 / 검증                                          #
# ---------------------------------------------------------------------------- #

def test_default_policy_matches_legacy_constants():
    """1-1. 기본값은 종전 src.planner.BUFFER_CASH=10_000, executor.SELL_TO_BUY_WAIT_SECONDS=3과 일치."""
    p = ExecutionPolicy()
    assert p.buffer_cash == 10_000
    assert p.sell_to_buy_wait_seconds == 3
    assert p.buy_cash_safety_ratio == 0.99
    # planner·executor의 호환 모듈 상수도 동일
    assert BUFFER_CASH == 10_000
    assert SELL_TO_BUY_WAIT_SECONDS == 3
    print('✅ 1-1 ExecutionPolicy 기본값: buffer_cash=10000, wait=3s, safety=0.99 (회귀 보장)')


def test_default_policy_singleton():
    """1-2. DEFAULT_EXECUTION_POLICY 인스턴스가 ExecutionPolicy()와 동등."""
    assert DEFAULT_EXECUTION_POLICY == ExecutionPolicy()
    assert isinstance(DEFAULT_EXECUTION_POLICY, ExecutionPolicy)
    print('✅ 1-2 DEFAULT_EXECUTION_POLICY: 기본 인스턴스와 동등')


def test_policy_is_frozen():
    """1-3. dataclass(frozen=True) — 불변 보장."""
    p = ExecutionPolicy()
    try:
        p.buffer_cash = 99
    except (AttributeError, Exception):
        print('✅ 1-3 ExecutionPolicy: frozen — 변경 불가')
        return
    raise AssertionError('frozen dataclass인데 attribute 변경 허용됨')


def test_policy_rejects_negative_buffer_cash():
    """1-4. buffer_cash < 0 → ValueError."""
    try:
        ExecutionPolicy(buffer_cash=-1)
    except ValueError:
        print('✅ 1-4 ExecutionPolicy: buffer_cash<0 → ValueError')
        return
    raise AssertionError('ValueError가 발생하지 않음')


def test_policy_rejects_negative_wait_seconds():
    """1-5. sell_to_buy_wait_seconds < 0 → ValueError."""
    try:
        ExecutionPolicy(sell_to_buy_wait_seconds=-1)
    except ValueError:
        print('✅ 1-5 ExecutionPolicy: wait<0 → ValueError')
        return
    raise AssertionError('ValueError가 발생하지 않음')


def test_policy_rejects_invalid_safety_ratio():
    """1-6. buy_cash_safety_ratio가 (0, 1] 범위 밖이면 ValueError."""
    for bad in [0.0, -0.1, 1.5, 2.0]:
        try:
            ExecutionPolicy(buy_cash_safety_ratio=bad)
        except ValueError:
            continue
        raise AssertionError(f'buy_cash_safety_ratio={bad}여도 통과됨')
    # 경계값 1.0은 허용
    ExecutionPolicy(buy_cash_safety_ratio=1.0)
    print('✅ 1-6 ExecutionPolicy: safety_ratio (0,1] 검증 (1.0 허용, 0/음수/>1.0 거부)')


# ---------------------------------------------------------------------------- #
# 세트 2 — PortfolioPlanner 주입                                                 #
# ---------------------------------------------------------------------------- #

def _make_planner(policy=None):
    """KISClient 인증 우회 + minimal allocation으로 PortfolioPlanner 인스턴스 생성."""
    kis_client = MagicMock()
    allocation = pd.DataFrame([
        {'ticker': '005930', 'weight': 1.0},
    ])
    return PortfolioPlanner(
        kis_client=kis_client,
        allocation_info=allocation,
        account_type='ISA',
        policy=policy,
    )


def test_planner_uses_default_policy_when_unset():
    """2-1. policy 미주입 시 DEFAULT_EXECUTION_POLICY 사용 (회귀 보장)."""
    p = _make_planner()
    assert p.policy is DEFAULT_EXECUTION_POLICY
    assert p.policy.buffer_cash == 10_000
    print('✅ 2-1 PortfolioPlanner: policy 미주입 → DEFAULT_EXECUTION_POLICY')


def test_planner_uses_custom_policy_buffer():
    """2-2. 커스텀 policy 주입 시 _create_total_info의 target_value 산정에 반영."""
    custom = ExecutionPolicy(buffer_cash=100_000)
    p = _make_planner(policy=custom)
    allocation = pd.DataFrame([
        {'ticker': '005930', 'weight': 1.0, 'current_price': 100},
    ])
    balance = pd.DataFrame([
        {'ticker': '005930', 'current_quantity': 0, 'current_value': 0},
        {'ticker': 'CASH', 'current_quantity': 0, 'current_value': 1_000_000},
    ])
    result = p._create_total_info(allocation, balance)
    # target_value = 1.0 × (1_000_000 - 100_000) = 900_000
    assert int(result.iloc[0]['target_value']) == 900_000, \
        f'기대 900_000, 실제 {result.iloc[0]["target_value"]}'
    print('✅ 2-2 PortfolioPlanner: 커스텀 buffer_cash=100000 → target_value 차감 반영')


def test_planner_default_buffer_matches_legacy_behavior():
    """2-3. 기본 policy로 _create_total_info 호출 시 종전(BUFFER_CASH=10000) 동작과 동일."""
    p = _make_planner()  # 기본 정책
    allocation = pd.DataFrame([
        {'ticker': '005930', 'weight': 1.0, 'current_price': 100},
    ])
    balance = pd.DataFrame([
        {'ticker': '005930', 'current_quantity': 0, 'current_value': 0},
        {'ticker': 'CASH', 'current_quantity': 0, 'current_value': 1_000_000},
    ])
    result = p._create_total_info(allocation, balance)
    # target_value = 1.0 × (1_000_000 - 10_000) = 990_000
    assert int(result.iloc[0]['target_value']) == 990_000
    print('✅ 2-3 PortfolioPlanner: 기본 정책 회귀 — target_value=990,000 (종전 동작)')


def test_planner_raises_when_total_le_buffer():
    """2-4. total_balance_value ≤ policy.buffer_cash → ValueError (가드 동작)."""
    custom = ExecutionPolicy(buffer_cash=100_000)
    p = _make_planner(policy=custom)
    allocation = pd.DataFrame([
        {'ticker': '005930', 'weight': 1.0, 'current_price': 100},
    ])
    balance = pd.DataFrame([
        {'ticker': '005930', 'current_quantity': 0, 'current_value': 0},
        {'ticker': 'CASH', 'current_quantity': 0, 'current_value': 50_000},
    ])
    try:
        p._create_total_info(allocation, balance)
    except ValueError as e:
        assert '버퍼' in str(e) or 'buffer' in str(e).lower()
        print('✅ 2-4 PortfolioPlanner: total ≤ buffer_cash → ValueError 가드')
        return
    raise AssertionError('ValueError가 발생하지 않음')


# ---------------------------------------------------------------------------- #
# 세트 3 — OrderExecutor 주입                                                    #
# ---------------------------------------------------------------------------- #

def _make_executor(policy=None, is_test: bool = False):
    """KISClient 인증 우회로 OrderExecutor 인스턴스 생성."""
    kis_client = MagicMock()
    return OrderExecutor(
        kis_client=kis_client,
        account_type='ISA',
        is_test=is_test,
        policy=policy,
    )


def test_executor_uses_default_policy_when_unset():
    """3-1. policy 미주입 시 DEFAULT_EXECUTION_POLICY 사용 (회귀 보장)."""
    ex = _make_executor()
    assert ex.policy is DEFAULT_EXECUTION_POLICY
    assert ex.policy.sell_to_buy_wait_seconds == 3
    assert ex.policy.buy_cash_safety_ratio == 0.99
    print('✅ 3-1 OrderExecutor: policy 미주입 → DEFAULT_EXECUTION_POLICY')


def test_executor_safety_ratio_used_in_orderable_qty():
    """3-2. _get_orderable_qty의 buy 분기가 self.policy.buy_cash_safety_ratio 사용.

    cash=1,000,000, calc=10,000, ratio=0.5 → available=500,000 / 10,000 = 50주.
    기본 0.99(=99주)와 다른 값으로 정책 주입 효과를 검증.
    """
    custom = ExecutionPolicy(buy_cash_safety_ratio=0.5)
    ex = _make_executor(policy=custom)
    mock_response = {'psbl_qty_calc_unpr': '10000'}
    ex.kis_client.fetch_domestic_enable_buy.return_value = mock_response
    qty, calc_price = ex._get_orderable_qty(
        ticker='005930', transaction_type='buy', available_cash=1_000_000,
    )
    assert qty == 50, f'기대 50주(ratio=0.5), 실제 {qty}'
    assert calc_price == 10_000
    print('✅ 3-2 OrderExecutor: 커스텀 buy_cash_safety_ratio=0.5 → 매수 가능 수량 변경')


def test_executor_wait_seconds_used_in_run_rebalancing():
    """3-3. run_rebalancing의 sell→buy sleep이 self.policy.sell_to_buy_wait_seconds 사용.

    sleep을 patch해 호출 인자가 정책 값과 일치하는지 검증 (실제로는 안 자도록).
    """
    import time as time_mod
    custom = ExecutionPolicy(sell_to_buy_wait_seconds=7)
    ex = _make_executor(policy=custom)

    # plan_df: sell 1건 + buy 1건 → sleep 호출 트리거
    plan_df = pd.DataFrame([
        {'ticker': '005930', 'required_transaction': 'sell', 'required_quantity': 1, 'current_price': 100},
        {'ticker': '035720', 'required_transaction': 'buy',  'required_quantity': 1, 'current_price': 100},
    ])
    # KIS 호출 모두 noop으로
    ex.kis_client.fetch_domestic_cash_balance.return_value = 100_000
    ex.kis_client.fetch_buy_orderable_cash.return_value = 100_000
    ex.kis_client.fetch_domestic_enable_buy.return_value = {'psbl_qty_calc_unpr': '100'}
    ex.kis_client.fetch_domestic_enable_sell.return_value = {'ord_psbl_qty': '5'}
    ex.kis_client.create_domestic_order.return_value = {'rt_cd': '0', 'msg1': 'ok'}

    from unittest.mock import patch
    with patch.object(time_mod, 'sleep') as mock_sleep:
        ex.run_rebalancing(plan_df)
    # sleep은 sell→buy 사이 1번 호출, 정책 값으로
    assert mock_sleep.call_count == 1, f'sleep 1회 기대, 실제 {mock_sleep.call_count}'
    assert mock_sleep.call_args.args[0] == 7, \
        f'sleep(7) 기대(custom 정책), 실제 sleep({mock_sleep.call_args.args[0]})'
    print('✅ 3-3 OrderExecutor: run_rebalancing이 self.policy.sell_to_buy_wait_seconds 사용')


def test_executor_default_safety_ratio_regression():
    """3-4. 기본 정책 회귀: cash=1,000,000, calc=10,000 → 99주 (종전 0.99 매직 넘버 동작)."""
    ex = _make_executor()  # 기본 정책
    mock_response = {'psbl_qty_calc_unpr': '10000'}
    ex.kis_client.fetch_domestic_enable_buy.return_value = mock_response
    qty, _ = ex._get_orderable_qty(
        ticker='005930', transaction_type='buy', available_cash=1_000_000,
    )
    assert qty == 99, f'기본 정책(0.99) 회귀 — 99주 기대, 실제 {qty}'
    print('✅ 3-4 OrderExecutor: 기본 정책 회귀 — 0.99 ratio 동작 보존')


# ---------------------------------------------------------------------------- #
# 세트 4 — StaticAllocator 합성 주입 (E2E)                                        #
# ---------------------------------------------------------------------------- #

def test_static_allocator_propagates_policy_to_both():
    """4-1. StaticAllocator(policy=...)가 Planner와 Executor 양쪽에 동일 인스턴스 주입."""
    from src.allocation import StaticAllocator
    custom = ExecutionPolicy(buffer_cash=50_000, sell_to_buy_wait_seconds=5)
    # KISClient 인증·토큰 흐름 우회
    from unittest.mock import patch
    with patch('src.allocation.KISClient') as mock_kis_cls:
        mock_kis_cls.return_value = MagicMock()
        allocation = pd.DataFrame([{'ticker': '005930', 'weight': 1.0}])
        allocator = StaticAllocator(
            account_type='ISA', allocation_info=allocation, is_test=True,
            policy=custom,
        )
    assert allocator.policy is custom
    assert allocator.planner.policy is custom
    assert allocator.executor.policy is custom
    print('✅ 4-1 StaticAllocator: 정책 인스턴스가 Planner·Executor 양쪽에 동일 주입')


# ---------------------------------------------------------------------------- #
# Runner                                                                       #
# ---------------------------------------------------------------------------- #
if __name__ == '__main__':
    test_default_policy_matches_legacy_constants()
    test_default_policy_singleton()
    test_policy_is_frozen()
    test_policy_rejects_negative_buffer_cash()
    test_policy_rejects_negative_wait_seconds()
    test_policy_rejects_invalid_safety_ratio()
    test_planner_uses_default_policy_when_unset()
    test_planner_uses_custom_policy_buffer()
    test_planner_default_buffer_matches_legacy_behavior()
    test_planner_raises_when_total_le_buffer()
    test_executor_uses_default_policy_when_unset()
    test_executor_safety_ratio_used_in_orderable_qty()
    test_executor_wait_seconds_used_in_run_rebalancing()
    test_executor_default_safety_ratio_regression()
    test_static_allocator_propagates_policy_to_both()
    print('\n🎉 모든 ARCH-007 ExecutionPolicy 주입 테스트 통과')
