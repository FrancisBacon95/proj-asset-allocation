"""BigQueryClient run_id / run_marker / row_type 단위 테스트 (ARCH-003).

검증:
- append_trade_log: 모든 row에 run_id, row_type='trade', account_type, update_dt, reg_date 자동 부여
- append_run_marker: row_type='run_marker', run_status, account_type, run_id 단일 행 적재
- is_already_executed: row_type 필터(run_marker/trade/NULL)가 SQL에 포함되어 진행 중인 run도 인식
- schema_update_options=ALLOW_FIELD_ADDITION 적용 — 신규 컬럼 자동 확장

실행: `uv run python -m test.test_bigquery_run_marker`
"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))


def _make_bq_client():
    """BigQueryClient를 인증 우회로 만들고 self.client/_table_ref 직접 주입."""
    from src.bigquery.client import BigQueryClient
    bq = BigQueryClient.__new__(BigQueryClient)
    bq.client = MagicMock()
    bq._table_ref = 'test_project.test_dataset.trade_log'
    return bq


# ---------------------------------------------------------------------------- #
# append_trade_log — run_id + row_type='trade' 자동 부여                         #
# ---------------------------------------------------------------------------- #

def test_append_trade_log_attaches_run_id_and_row_type():
    """run_id 인자가 모든 행에 박히고 row_type='trade'로 라벨 됨."""
    bq = _make_bq_client()
    df = pd.DataFrame([
        {'ticker': '005930', 'requested_quantity': 5, 'filled_quantity': 5, 'is_success': True},
        {'ticker': '000660', 'requested_quantity': 3, 'filled_quantity': 0, 'is_success': False},
    ])

    bq.append_trade_log(df, account_type='ISA', run_id='test_run_abc')

    # load_table_from_dataframe 1회 호출
    assert bq.client.load_table_from_dataframe.call_count == 1
    call_args = bq.client.load_table_from_dataframe.call_args
    uploaded_df = call_args.args[0]

    # 모든 행에 run_id, row_type, account_type 박힘
    assert (uploaded_df['run_id'] == 'test_run_abc').all(), 'run_id 모든 행 박혀야 함'
    assert (uploaded_df['row_type'] == 'trade').all(), 'row_type=trade 모든 행 박혀야 함'
    assert (uploaded_df['account_type'] == 'ISA').all()
    assert 'update_dt' in uploaded_df.columns
    assert 'reg_date' in uploaded_df.columns
    print('✅ append_trade_log: run_id + row_type=trade 모든 행 자동 부여')


def test_append_trade_log_without_run_id_omits_column():
    """run_id 인자 부재 시 run_id 컬럼 자체를 추가하지 않음 (기존 호출 호환)."""
    bq = _make_bq_client()
    df = pd.DataFrame([{'ticker': '005930', 'requested_quantity': 5}])

    bq.append_trade_log(df, account_type='ISA')  # run_id 인자 없음

    uploaded_df = bq.client.load_table_from_dataframe.call_args.args[0]
    assert 'run_id' not in uploaded_df.columns, 'run_id 인자 없으면 컬럼도 없어야 함 (기존 호출 호환)'
    assert (uploaded_df['row_type'] == 'trade').all(), 'row_type은 항상 부여'
    print('✅ append_trade_log: run_id 부재 시 컬럼 부재 (기존 호출 호환)')


def test_append_trade_log_uses_schema_update_options():
    """LoadJobConfig에 ALLOW_FIELD_ADDITION 포함 (자동 컬럼 확장)."""
    from google.cloud import bigquery
    bq = _make_bq_client()
    df = pd.DataFrame([{'ticker': '005930', 'qty': 5}])

    bq.append_trade_log(df, account_type='ISA', run_id='r1')

    job_config = bq.client.load_table_from_dataframe.call_args.kwargs['job_config']
    assert bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION in job_config.schema_update_options, \
        'ALLOW_FIELD_ADDITION 미적용 시 신규 컬럼(requested_quantity 등) 자동 추가 안 됨'
    print('✅ append_trade_log: schema_update_options=ALLOW_FIELD_ADDITION 적용')


# ---------------------------------------------------------------------------- #
# append_run_marker                                                            #
# ---------------------------------------------------------------------------- #

def test_append_run_marker_writes_single_row():
    """append_run_marker: row_type='run_marker', run_status, run_id, account_type 1행 적재."""
    bq = _make_bq_client()
    bq.append_run_marker(run_id='run_xyz', account_type='ISA', status='started')

    assert bq.client.load_table_from_dataframe.call_count == 1
    df = bq.client.load_table_from_dataframe.call_args.args[0]
    assert len(df) == 1, 'run_marker는 단일 행 적재'
    row = df.iloc[0]
    assert row['run_id'] == 'run_xyz'
    assert row['row_type'] == 'run_marker', "row_type='run_marker' 필수 (is_already_executed 필터에 사용)"
    assert row['run_status'] == 'started'
    assert row['account_type'] == 'ISA'
    print('✅ append_run_marker: row_type/run_status/run_id/account_type 1행 적재')


def test_append_run_marker_completed_status():
    """status='completed'도 동일하게 동작."""
    bq = _make_bq_client()
    bq.append_run_marker(run_id='run_xyz', account_type='ISA', status='completed')

    df = bq.client.load_table_from_dataframe.call_args.args[0]
    assert df.iloc[0]['run_status'] == 'completed'
    print('✅ append_run_marker: status=completed 정상 적재')


# ---------------------------------------------------------------------------- #
# is_already_executed — row_type 필터                                          #
# ---------------------------------------------------------------------------- #

def test_is_already_executed_includes_row_type_filter():
    """is_already_executed의 SQL에 row_type IN ('run_marker','trade') OR NULL 포함.

    이게 빠지면 진행 중인 run(=marker만 적재된 상태)을 인지 못해 중복 실행 발생.
    """
    bq = _make_bq_client()
    fake_result = MagicMock()
    fake_result.__iter__ = lambda self: iter([MagicMock(cnt=1)])
    bq.client.query.return_value.result.return_value = fake_result

    bq.is_already_executed(account_type='ISA', target_date=date(2026, 5, 9))

    sql = bq.client.query.call_args.args[0]
    # row_type 필터가 SQL에 포함됨
    assert 'row_type' in sql, 'is_already_executed SQL에 row_type 필터 누락'
    assert "row_type IN ('run_marker','trade')" in sql or 'IN (\'run_marker\',\'trade\')' in sql, \
        f'row_type 필터 형식이 기대와 다름. SQL:\n{sql}'
    assert 'IS NULL' in sql, '마이그레이션 호환을 위해 row_type IS NULL 허용 필수'
    print('✅ is_already_executed: row_type IN (run_marker, trade) OR NULL 필터 포함')


def test_is_already_executed_returns_true_for_running_run_marker():
    """run_marker만 적재된 상태(진행 중인 run)에서도 True 반환 — 중복 실행 방지."""
    bq = _make_bq_client()
    # cnt=1 (run_marker가 1개 있음)
    fake_result = MagicMock()
    fake_result.__iter__ = lambda self: iter([MagicMock(cnt=1)])
    bq.client.query.return_value.result.return_value = fake_result

    result = bq.is_already_executed(account_type='ISA', target_date=date(2026, 5, 9))
    assert result is True, 'run_marker(진행중)만 있어도 True (중복 실행 차단)'
    print('✅ is_already_executed: run_marker만 있어도 True (진행중인 run 인식)')


def test_is_already_executed_returns_false_when_count_zero():
    """카운트 0이면 False — 정상 실행 진입."""
    bq = _make_bq_client()
    fake_result = MagicMock()
    fake_result.__iter__ = lambda self: iter([MagicMock(cnt=0)])
    bq.client.query.return_value.result.return_value = fake_result

    result = bq.is_already_executed(account_type='ISA', target_date=date(2026, 5, 9))
    assert result is False
    print('✅ is_already_executed: count=0 → False')


if __name__ == '__main__':
    test_append_trade_log_attaches_run_id_and_row_type()
    test_append_trade_log_without_run_id_omits_column()
    test_append_trade_log_uses_schema_update_options()
    test_append_run_marker_writes_single_row()
    test_append_run_marker_completed_status()
    test_is_already_executed_includes_row_type_filter()
    test_is_already_executed_returns_true_for_running_run_marker()
    test_is_already_executed_returns_false_when_count_zero()
    print('\n전체 테스트 통과')
