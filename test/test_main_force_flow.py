"""main.py 실행 모드 결정 흐름 단위 테스트 (ARCH-002 + ARCH-003).

검증:
- ARCH-002: --force일 때 is_market_open/is_already_executed 외부 호출 자체를 건너뛴다.
- ARCH-003: 실행 시 run_id가 발급되고 BQ marker(started/completed) + trade_log에 동일 run_id로 그룹.
- IRP·is_test 케이스에서 BQ 호출 스킵 회귀.

main.py를 직접 import해 main()을 호출 — 외부 I/O는 모두 모킹.

실행: `uv run python -m test.test_main_force_flow`
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))


def _mock_main_dependencies(account_type: str = 'ISA', acc_no_postfix: str = '01',
                            args_test: bool = False, args_force: bool = False):
    """main.main()을 호출하기 위한 모든 외부 의존성을 모킹한 컨텍스트 dict 반환.

    호출자가 with patch.multiple(...) 형태로 일괄 적용.
    """
    # GoogleSheetsClient
    mock_gs = MagicMock()
    mock_gs.get_df_from_google_sheets.return_value = pd.DataFrame([
        {'ticker': '005930', 'weight': '1.0'},
    ])
    mock_gs.get_worksheet_url.return_value = 'https://docs.google.com/sheets/test'

    # BigQueryClient
    mock_bq = MagicMock()
    mock_bq.is_already_executed.return_value = False

    # StaticAllocator
    mock_allocator = MagicMock()
    mock_allocator.planner.kis_client.is_irp.return_value = (acc_no_postfix == '29')
    mock_allocator.is_trading_day.return_value = True
    mock_allocator.run.return_value = (
        pd.DataFrame([{
            'ticker': '005930', 'stock_nm': '삼성전자',
            'required_transaction': 'buy', 'required_quantity': 5,
            'transaction_quantity': 5, 'enable_quantity': 5,
            'is_success': True, 'response_msg': 'ok', 'transaction_order': 0,
            'current_price': 70000, 'current_value': 0, 'current_pct': 0,
            'weight': 1.0,
        }]),
        100_000,
    )

    # 가짜 args
    fake_args = MagicMock()
    fake_args.account_type = account_type
    fake_args.test = args_test
    fake_args.force = args_force

    return {
        'mock_gs': mock_gs, 'mock_bq': mock_bq,
        'mock_allocator': mock_allocator, 'fake_args': fake_args,
    }


def _run_main_with_mocks(deps: dict):
    """main()을 deps 모킹과 함께 호출."""
    import main as main_module
    with patch.object(main_module, 'GoogleSheetsClient', return_value=deps['mock_gs']), \
         patch.object(main_module, 'BigQueryClient', return_value=deps['mock_bq']), \
         patch.object(main_module, 'StaticAllocator', return_value=deps['mock_allocator']), \
         patch.object(main_module, 'slack_notify'), \
         patch.object(main_module, '_parse_args', return_value=deps['fake_args']):
        main_module.main()


# ---------------------------------------------------------------------------- #
# ARCH-002 — force flow                                                        #
# ---------------------------------------------------------------------------- #

def test_force_skips_is_trading_day_and_is_already_executed():
    """--force 시 외부 조회(is_trading_day, is_already_executed) 호출 0."""
    deps = _mock_main_dependencies(account_type='ISA', acc_no_postfix='01', args_force=True)
    _run_main_with_mocks(deps)

    # 핵심: 외부 조회 메서드는 한 번도 호출되면 안 됨
    assert deps['mock_allocator'].is_trading_day.call_count == 0, \
        '--force 시 is_trading_day 호출 금지'
    assert deps['mock_bq'].is_already_executed.call_count == 0, \
        '--force 시 is_already_executed 호출 금지 (외부 BQ 트래픽 절약)'
    # allocator.run은 정상 호출됨
    assert deps['mock_allocator'].run.call_count == 1
    print('✅ ARCH-002 --force: is_trading_day / is_already_executed 호출 0 + run() 진입')


def test_test_mode_skips_only_is_already_executed():
    """--test 시 is_already_executed만 스킵, is_trading_day는 호출 (정보 로깅용)."""
    deps = _mock_main_dependencies(account_type='ISA', acc_no_postfix='01', args_test=True)
    _run_main_with_mocks(deps)

    assert deps['mock_allocator'].is_trading_day.call_count == 1, '--test 시도 is_trading_day는 호출'
    assert deps['mock_bq'].is_already_executed.call_count == 0, '--test 시 BQ 조회 금지'
    print('✅ --test: is_trading_day 호출 + is_already_executed 스킵')


def test_normal_mode_calls_both_external_checks():
    """일반 모드(--test/--force 모두 없음): is_trading_day + is_already_executed 모두 호출."""
    deps = _mock_main_dependencies(account_type='ISA', acc_no_postfix='01')
    _run_main_with_mocks(deps)

    assert deps['mock_allocator'].is_trading_day.call_count == 1
    assert deps['mock_bq'].is_already_executed.call_count == 1, \
        '일반 모드는 BQ 중복 체크 필수'
    print('✅ 일반 모드: is_trading_day + is_already_executed 모두 호출 (회귀)')


def test_irp_skips_is_already_executed_even_without_force():
    """IRP(postfix=29)는 BQ 거래 이력이 없어 is_already_executed 호출 금지 (회귀, Phase 2 동작 유지)."""
    deps = _mock_main_dependencies(account_type='IRP', acc_no_postfix='29')
    _run_main_with_mocks(deps)

    assert deps['mock_allocator'].is_trading_day.call_count == 1, 'IRP도 is_trading_day는 호출'
    assert deps['mock_bq'].is_already_executed.call_count == 0, 'IRP는 BQ 조회 스킵 (회귀)'
    print('✅ IRP: is_already_executed 호출 0 (회귀)')


# ---------------------------------------------------------------------------- #
# ARCH-003 — run_id + run_marker                                               #
# ---------------------------------------------------------------------------- #

def test_run_id_propagates_to_marker_and_trade_log():
    """ISA 정상 흐름: run_id 발급 후 marker(started/completed) + trade_log 모두 동일 run_id로 적재."""
    deps = _mock_main_dependencies(account_type='ISA', acc_no_postfix='01')
    _run_main_with_mocks(deps)

    bq = deps['mock_bq']
    # marker 호출 2회: started + completed
    assert bq.append_run_marker.call_count == 2, f'marker 2회(started+completed) 기대, 실제 {bq.append_run_marker.call_count}'
    started_call = bq.append_run_marker.call_args_list[0]
    completed_call = bq.append_run_marker.call_args_list[1]
    assert started_call.kwargs.get('status') == 'started' or 'started' in started_call.args, \
        f'첫 marker는 started여야 함, 실제 {started_call}'
    assert completed_call.kwargs.get('status') == 'completed' or 'completed' in completed_call.args, \
        f'두 번째 marker는 completed여야 함, 실제 {completed_call}'

    # 동일 run_id 사용 — args[0] 또는 kwargs['run_id']
    started_run_id = started_call.args[0] if started_call.args else started_call.kwargs.get('run_id')
    completed_run_id = completed_call.args[0] if completed_call.args else completed_call.kwargs.get('run_id')
    assert started_run_id == completed_run_id, 'started/completed marker는 같은 run_id 공유'

    # trade_log도 동일 run_id로 적재
    assert bq.append_trade_log.call_count == 1
    trade_log_run_id = bq.append_trade_log.call_args.kwargs.get('run_id')
    assert trade_log_run_id == started_run_id, 'trade_log run_id가 marker run_id와 동일해야 함'

    # 호출 순서: started → run() → trade_log → completed (외부에서는 marker 2번 + trade_log 1번)
    print('✅ ARCH-003: run_id 발급 + started/completed marker + trade_log 동일 run_id 그룹')


def test_irp_skips_run_marker_and_trade_log():
    """IRP는 BQ 거래 이력 부재 정책 — run_marker도 스킵."""
    deps = _mock_main_dependencies(account_type='IRP', acc_no_postfix='29')
    _run_main_with_mocks(deps)

    bq = deps['mock_bq']
    assert bq.append_run_marker.call_count == 0, 'IRP는 run_marker 적재 금지'
    assert bq.append_trade_log.call_count == 0, 'IRP는 trade_log 적재 금지'
    # IRP는 Sheets에 IRP_action_plan 덮어쓰기
    assert deps['mock_gs'].overwrite_dataframe.call_count == 1
    print('✅ IRP: run_marker / trade_log 적재 0 + Sheets overwrite ✅')


def test_test_mode_skips_run_marker():
    """--test 모드: bq_client=None이므로 run_marker/trade_log 적재 0."""
    deps = _mock_main_dependencies(account_type='ISA', acc_no_postfix='01', args_test=True)
    # is_test=True에서는 main.py가 BigQueryClient 인스턴스 자체를 만들지 않으므로
    # mock_bq가 호출되지 않는지 확인
    _run_main_with_mocks(deps)

    bq = deps['mock_bq']
    assert bq.append_run_marker.call_count == 0, '--test에서는 run_marker 적재 금지'
    assert bq.append_trade_log.call_count == 0
    print('✅ --test: run_marker / trade_log 적재 0')


if __name__ == '__main__':
    test_force_skips_is_trading_day_and_is_already_executed()
    test_test_mode_skips_only_is_already_executed()
    test_normal_mode_calls_both_external_checks()
    test_irp_skips_is_already_executed_even_without_force()
    test_run_id_propagates_to_marker_and_trade_log()
    test_irp_skips_run_marker_and_trade_log()
    test_test_mode_skips_run_marker()
    print('\n전체 테스트 통과')
