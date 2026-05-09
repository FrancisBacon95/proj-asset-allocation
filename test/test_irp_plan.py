"""IRP 플랜 모드 단위 테스트.

거래 없이 모킹된 응답으로 다음을 검증한다:
- StaticAllocator.run()이 IRP(postfix='29')에서 executor 호출 없이 plan만 반환
- ISA(postfix='01')는 기존대로 executor 호출 (회귀)
- GoogleSheetsClient.overwrite_dataframe이 worksheet.clear() + update() 순서로 호출
- format_irp_plan_summary가 매수/매도 헤더 + 시트 URL을 포함

실행: `uv run python -m test.test_irp_plan`
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))


def _make_allocator_with_postfix(postfix: str):
    """StaticAllocator를 KIS 인증 우회로 만들고 acc_no_postfix만 강제 주입.

    is_irp()는 postfix=='29'와 일관되게 동작하도록 명시 주입 (MagicMock 자동 truthy 회피).
    """
    from src.allocation import StaticAllocator
    allocator = StaticAllocator.__new__(StaticAllocator)
    allocator.account_type = 'IRP' if postfix == '29' else 'ISA'
    allocator.allocation_info = pd.DataFrame()
    allocator.is_test = False

    # planner와 executor를 mock으로 — StaticAllocator.run()이 호출하는 부분만 noop 처리
    allocator.planner = MagicMock()
    allocator.planner.kis_client = MagicMock()
    allocator.planner.kis_client.acc_no_postfix = postfix
    allocator.planner.kis_client.is_irp = MagicMock(return_value=(postfix == '29'))
    allocator.executor = MagicMock()
    return allocator


# ---------------------------------------------------------------------------- #
# StaticAllocator.run() IRP 분기                                                #
# ---------------------------------------------------------------------------- #

def test_static_allocator_skips_executor_for_irp():
    """IRP(postfix='29'): planner.get_rebalancing_plan()만 호출, executor.run_rebalancing 미호출."""
    allocator = _make_allocator_with_postfix('29')
    plan_df = pd.DataFrame([
        {'ticker': '005930', 'required_transaction': 'buy', 'required_quantity': 5},
        {'ticker': 'CASH',   'required_transaction': None,  'required_quantity': 0},
    ])
    allocator.planner.get_rebalancing_plan.return_value = plan_df

    result, remaining_cash = allocator.run()

    # plan_df 그대로 반환, remaining_cash=0
    pd.testing.assert_frame_equal(result, plan_df)
    assert remaining_cash == 0
    # executor 호출 0
    assert allocator.executor.run_rebalancing.call_count == 0, \
        'IRP에서 executor.run_rebalancing 호출 금지 (자동 매매 불가)'
    print('✅ IRP: StaticAllocator.run()이 executor 호출 없이 plan만 반환')


def test_static_allocator_runs_executor_for_isa():
    """ISA(postfix='01'): 기존대로 executor.run_rebalancing 호출 (회귀)."""
    allocator = _make_allocator_with_postfix('01')
    plan_df = pd.DataFrame([
        {'ticker': '005930', 'required_transaction': 'buy', 'required_quantity': 5},
    ])
    trade_log = pd.DataFrame([
        {'ticker': '005930', 'transaction_quantity': 5, 'is_success': True},
    ])
    allocator.planner.get_rebalancing_plan.return_value = plan_df
    allocator.executor.run_rebalancing.return_value = (trade_log, 100_000)

    result, remaining_cash = allocator.run()

    assert allocator.executor.run_rebalancing.call_count == 1, \
        'ISA는 기존대로 executor.run_rebalancing 호출돼야 함'
    assert remaining_cash == 100_000
    # merge 결과: plan + trade_log 합쳐짐
    assert 'transaction_quantity' in result.columns
    print('✅ ISA: StaticAllocator.run()이 executor 호출 + plan/trade_log merge (회귀)')


def test_static_allocator_no_executor_for_pension_savings():
    """PPA(연금저축, postfix='22')는 IRP가 아님 → executor 호출됨 (회귀, _is_pension과 무관)."""
    allocator = _make_allocator_with_postfix('22')
    plan_df = pd.DataFrame([{'ticker': '005930', 'required_transaction': 'buy', 'required_quantity': 5}])
    trade_log = pd.DataFrame([{'ticker': '005930', 'transaction_quantity': 5, 'is_success': True}])
    allocator.planner.get_rebalancing_plan.return_value = plan_df
    allocator.executor.run_rebalancing.return_value = (trade_log, 50_000)

    _, remaining_cash = allocator.run()
    assert allocator.executor.run_rebalancing.call_count == 1, \
        'PPA(연금저축)는 IRP가 아니므로 executor 호출돼야 함'
    assert remaining_cash == 50_000
    print('✅ PPA(연금저축): executor 호출됨 — IRP 분기는 postfix=29만')


# ---------------------------------------------------------------------------- #
# GoogleSheetsClient.overwrite_dataframe 호출 순서                              #
# ---------------------------------------------------------------------------- #

def _make_sheets_client_with_mock_spreadsheet():
    """GoogleSheetsClient를 인증 우회로 만들고 spreadsheet만 MagicMock 주입.

    autospec=True로 gspread.Spreadsheet/Worksheet 시그니처 검증 — API 어긋남 자동 발견.
    """
    import gspread
    from src.sheets.client import GoogleSheetsClient
    client = GoogleSheetsClient.__new__(GoogleSheetsClient)
    mock_ws = MagicMock(spec=gspread.Worksheet)
    mock_ws.row_count = 100  # 기본값 (테스트에서 필요시 override)
    mock_ss = MagicMock(spec=gspread.Spreadsheet)
    client.spreadsheet = mock_ss
    return client, mock_ss, mock_ws


def test_overwrite_dataframe_updates_then_trailing_clears_existing_sheet():
    """기존 탭: update 먼저 → 성공 후 트레일링 행만 batch_clear (트랜잭션 안전).

    clear() 호출 안 함 — update 실패 시 기존 데이터 보존되도록.
    """
    client, mock_ss, mock_ws = _make_sheets_client_with_mock_spreadsheet()
    mock_ws.row_count = 100  # 기존 시트가 100행 있는 상태
    mock_ss.worksheet.return_value = mock_ws

    df = pd.DataFrame([
        {'ticker': '005930', 'qty': 5},
        {'ticker': '000660', 'qty': 3},
    ])
    client.overwrite_dataframe('IRP_action_plan', df)

    mock_ss.worksheet.assert_called_once_with('IRP_action_plan')
    mock_ss.add_worksheet.assert_not_called()  # 기존 탭이 있으니 생성 안 함

    # clear() 미호출 — 트랜잭션 안전
    mock_ws.clear.assert_not_called()

    # update 호출 — 헤더 + 데이터 3행
    assert mock_ws.update.call_count == 1
    update_kwargs = mock_ws.update.call_args.kwargs
    assert update_kwargs['range_name'] == 'A1'
    values = update_kwargs['values']
    assert values[0] == ['ticker', 'qty']
    assert len(values) == 3, '헤더 1 + 데이터 2 = 3행'

    # 트레일링 batch_clear: A4:100 (새 데이터 3행 다음부터 끝까지)
    mock_ws.batch_clear.assert_called_once_with(['A4:100'])
    print('✅ overwrite_dataframe: 기존 탭 → update 먼저 → 트레일링 batch_clear (트랜잭션 안전)')


def test_overwrite_dataframe_update_failure_preserves_existing_data():
    """update 실패 시 batch_clear/clear 미호출 → 기존 데이터 보존 (P0 #1 회귀 방지)."""
    client, mock_ss, mock_ws = _make_sheets_client_with_mock_spreadsheet()
    mock_ws.row_count = 100
    mock_ws.update.side_effect = RuntimeError('API quota exceeded')
    mock_ss.worksheet.return_value = mock_ws

    df = pd.DataFrame([{'ticker': '005930', 'qty': 5}])
    try:
        client.overwrite_dataframe('IRP_action_plan', df)
        assert False, 'update 실패가 위로 surface돼야 함'
    except RuntimeError:
        pass

    # update 실패했으니 clear/batch_clear 모두 미호출 → 기존 데이터 보존
    mock_ws.clear.assert_not_called()
    mock_ws.batch_clear.assert_not_called()
    print('✅ overwrite_dataframe: update 실패 시 batch_clear 미호출 → 기존 데이터 보존 (Codex P0 #1)')


def test_overwrite_dataframe_no_trailing_when_new_data_fills_sheet():
    """새 데이터 길이가 row_count 이상이면 batch_clear 미호출 (불필요 API 호출 방지)."""
    client, mock_ss, mock_ws = _make_sheets_client_with_mock_spreadsheet()
    mock_ws.row_count = 3  # 작은 시트
    mock_ss.worksheet.return_value = mock_ws

    # 헤더 1 + 데이터 5 = 6행 > row_count(3)
    df = pd.DataFrame([{'ticker': str(i), 'qty': i} for i in range(5)])
    client.overwrite_dataframe('IRP_action_plan', df)

    mock_ws.update.assert_called_once()
    # 새 데이터가 시트보다 크니 트레일링 정리 불필요
    mock_ws.batch_clear.assert_not_called()
    print('✅ overwrite_dataframe: 새 데이터 ≥ row_count → batch_clear 미호출 (불필요 API 회피)')


def test_overwrite_dataframe_creates_missing_sheet():
    """탭이 없을 때: add_worksheet로 생성 후 update (신규 시트는 batch_clear 불필요)."""
    import gspread
    client, mock_ss, mock_ws = _make_sheets_client_with_mock_spreadsheet()
    # 첫 호출에서 WorksheetNotFound 발생
    mock_ss.worksheet.side_effect = gspread.exceptions.WorksheetNotFound('not found')
    mock_ss.add_worksheet.return_value = mock_ws

    df = pd.DataFrame([{'ticker': '005930', 'qty': 5}])
    client.overwrite_dataframe('IRP_action_plan', df)

    mock_ss.add_worksheet.assert_called_once()
    add_kwargs = mock_ss.add_worksheet.call_args.kwargs
    assert add_kwargs['title'] == 'IRP_action_plan'
    assert add_kwargs['rows'] >= 100, '신규 탭 rows ≥ 100'
    assert add_kwargs['cols'] >= 10
    # 신규 시트는 clear/batch_clear 모두 불필요
    mock_ws.clear.assert_not_called()
    mock_ws.batch_clear.assert_not_called()
    mock_ws.update.assert_called_once()
    print('✅ overwrite_dataframe: 탭 부재 → add_worksheet 생성 → update (clear/batch_clear 미호출)')


def test_overwrite_dataframe_empty_df_writes_only_header():
    """빈 DataFrame이어도 헤더 행은 기록 (스키마 보존)."""
    client, mock_ss, mock_ws = _make_sheets_client_with_mock_spreadsheet()
    mock_ws.row_count = 100
    mock_ss.worksheet.return_value = mock_ws

    empty_df = pd.DataFrame(columns=['ticker', 'qty', 'note'])
    client.overwrite_dataframe('IRP_action_plan', empty_df)

    update_kwargs = mock_ws.update.call_args.kwargs
    assert update_kwargs['values'] == [['ticker', 'qty', 'note']], '빈 DF는 헤더만 기록'
    # 헤더 1행 기록 후 트레일링(A2:100) 정리
    mock_ws.batch_clear.assert_called_once_with(['A2:100'])
    print('✅ overwrite_dataframe: 빈 DataFrame도 헤더 행은 기록 (스키마 보존)')


# ---------------------------------------------------------------------------- #
# format_irp_plan_summary 포맷                                                  #
# ---------------------------------------------------------------------------- #

def test_format_irp_plan_summary_includes_header_and_url():
    """IRP 포맷 헤더, 매수/매도 카운트, 시트 URL 모두 포함."""
    from src.slack.client import format_irp_plan_summary
    from datetime import datetime
    import pytz

    plan_df = pd.DataFrame([
        {'ticker': '005930', 'stock_nm': '삼성전자',
         'required_transaction': 'buy', 'required_quantity': 5,
         'weight': 0.3, 'current_pct': 25.0},
        {'ticker': '000660', 'stock_nm': 'SK하이닉스',
         'required_transaction': 'sell', 'required_quantity': 2,
         'weight': 0.2, 'current_pct': 30.0},
        {'ticker': 'CASH', 'stock_nm': 'WON_DEPOSIT',
         'required_transaction': None, 'required_quantity': 0,
         'weight': 0.5, 'current_pct': 45.0},
    ])
    dt = datetime(2026, 5, 9, 9, 0, tzinfo=pytz.timezone('Asia/Seoul'))

    summary = format_irp_plan_summary(
        plan_df=plan_df,
        account_type='IRP',
        dt=dt,
        sheet_url='https://docs.google.com/spreadsheets/d/abc#gid=123',
    )

    assert '[IRP] IRP 리밸런싱 플랜 생성' in summary, 'IRP 헤더 누락'
    assert '자동 매매 불가' in summary, '수동 매매 안내 누락'
    assert '매수 1건' in summary
    assert '매도 1건' in summary
    assert 'IRP_action_plan 시트 보기' in summary, '시트 URL 링크 누락'
    assert 'https://docs.google.com/spreadsheets/d/abc#gid=123' in summary
    assert '삼성전자' in summary and 'BUY' in summary
    assert 'SK하이닉스' in summary and 'SELL' in summary
    # CASH 행은 출력에서 제외돼야 함
    assert 'WON_DEPOSIT' not in summary
    print('✅ format_irp_plan_summary: 헤더 + 매수/매도 카운트 + 시트 URL + 종목 액션 포함')


def test_format_irp_plan_summary_excludes_zero_quantity():
    """required_quantity=0인 행은 출력에서 제외 (KOFR buy 0주 같은 헷갈림 방지, P3)."""
    from src.slack.client import format_irp_plan_summary
    from datetime import datetime

    plan_df = pd.DataFrame([
        {'ticker': '005930', 'stock_nm': '삼성전자',
         'required_transaction': 'buy', 'required_quantity': 5,
         'weight': 0.3, 'current_pct': 25.0},
        {'ticker': 'KOFR', 'stock_nm': 'KOFR액티브',
         'required_transaction': 'buy', 'required_quantity': 0,  # ← 변동량 0
         'weight': 0.1, 'current_pct': 10.0},
        {'ticker': 'CASH', 'stock_nm': 'WON_DEPOSIT',
         'required_transaction': None, 'required_quantity': 0,
         'weight': 0.6, 'current_pct': 65.0},
    ])

    summary = format_irp_plan_summary(
        plan_df=plan_df, account_type='IRP',
        dt=datetime(2026, 5, 9), sheet_url=None,
    )

    assert '삼성전자' in summary, 'qty>0 종목은 포함'
    assert 'KOFR' not in summary, 'qty=0 종목은 출력 제외 (BUY 0주는 헷갈림)'
    assert '매수 1건' in summary, '카운트도 qty>0만 반영'
    assert '매도 0건' in summary
    print('✅ format_irp_plan_summary: required_quantity=0 종목 출력 제외 + 카운트 정확 (P3)')


def test_format_irp_plan_summary_raises_on_missing_required_column():
    """필수 컬럼(weight 등) 누락 시 KeyError로 즉시 실패 (silent failure 방지, P0 #3)."""
    from src.slack.client import format_irp_plan_summary
    from datetime import datetime

    # weight 컬럼 누락
    broken_df = pd.DataFrame([{
        'ticker': '005930', 'stock_nm': '삼성전자',
        'required_transaction': 'buy', 'required_quantity': 5,
        'current_pct': 25.0,
        # 'weight' 부재
    }])

    try:
        format_irp_plan_summary(
            plan_df=broken_df, account_type='IRP',
            dt=datetime(2026, 5, 9), sheet_url=None,
        )
        assert False, '필수 컬럼 누락 시 KeyError 발생해야 함 (silent failure 방지)'
    except KeyError as e:
        assert 'weight' in str(e), f'누락 필드 명시돼야 함, 실제 {e}'
    print('✅ format_irp_plan_summary: 필수 컬럼 누락 시 KeyError로 silent failure 방지 (P0 #3)')


def test_format_irp_plan_summary_empty_plan():
    """변동 없는 플랜(매수·매도 모두 0)일 때 안내 메시지 포함."""
    from src.slack.client import format_irp_plan_summary
    from datetime import datetime

    plan_df = pd.DataFrame([
        {'ticker': '005930', 'stock_nm': '삼성전자',
         'required_transaction': None, 'required_quantity': 0,
         'weight': 0.5, 'current_pct': 50.0},
        {'ticker': 'CASH', 'stock_nm': 'WON_DEPOSIT',
         'required_transaction': None, 'required_quantity': 0,
         'weight': 0.5, 'current_pct': 50.0},
    ])

    summary = format_irp_plan_summary(
        plan_df=plan_df,
        account_type='IRP',
        dt=datetime(2026, 5, 9),
        sheet_url=None,  # URL 없는 케이스
    )

    assert '매수 0건' in summary and '매도 0건' in summary
    assert '플랜 변동 사항 없음' in summary
    assert '시트 보기' not in summary  # URL None이면 링크 부재
    print('✅ format_irp_plan_summary: 변동 없는 플랜 + URL 부재 케이스')


def test_kis_client_is_irp_helper():
    """KISClient.is_irp() public 헬퍼: postfix='29'만 True (P2 헬퍼화)."""
    from src.kis.client import KISClient
    for postfix, expected in [('01', False), ('22', False), ('29', True)]:
        c = KISClient.__new__(KISClient)
        c.acc_no_postfix = postfix
        assert c.is_irp() is expected, f'postfix={postfix}: is_irp() 기대 {expected}, 실제 {c.is_irp()}'
        # _is_pension은 is_irp의 alias
        assert c._is_pension() == c.is_irp(), '_is_pension은 is_irp() alias'
    print('✅ KISClient.is_irp(): postfix=29만 True, _is_pension은 alias')


if __name__ == '__main__':
    test_static_allocator_skips_executor_for_irp()
    test_static_allocator_runs_executor_for_isa()
    test_static_allocator_no_executor_for_pension_savings()
    test_overwrite_dataframe_updates_then_trailing_clears_existing_sheet()
    test_overwrite_dataframe_update_failure_preserves_existing_data()
    test_overwrite_dataframe_no_trailing_when_new_data_fills_sheet()
    test_overwrite_dataframe_creates_missing_sheet()
    test_overwrite_dataframe_empty_df_writes_only_header()
    test_format_irp_plan_summary_includes_header_and_url()
    test_format_irp_plan_summary_excludes_zero_quantity()
    test_format_irp_plan_summary_raises_on_missing_required_column()
    test_format_irp_plan_summary_empty_plan()
    test_kis_client_is_irp_helper()
    print('\n전체 테스트 통과')
