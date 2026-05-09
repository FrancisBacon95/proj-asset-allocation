from datetime import datetime, timedelta

import pandas as pd
import pytz
from google.api_core.exceptions import NotFound
from google.auth import default
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

from src.config.env import BQ_DATASET_ID, BQ_PROJECT_ID, EXECUTE_ENV, GCP_KEY_PATH
from src.logger import get_logger

logger = get_logger(__name__)

_BQ_SCOPES = ['https://www.googleapis.com/auth/bigquery']


class BigQueryClient:
    def __init__(self) -> None:
        if EXECUTE_ENV == 'LOCAL':
            credentials = Credentials.from_service_account_file(GCP_KEY_PATH, scopes=_BQ_SCOPES)
        else:
            credentials, _ = default(scopes=_BQ_SCOPES)

        self.client = bigquery.Client(project=BQ_PROJECT_ID, credentials=credentials)
        self._table_ref = f'{BQ_PROJECT_ID}.{BQ_DATASET_ID}.trade_log'

    def append_trade_log(self, df: pd.DataFrame, account_type: str, run_id: str = None) -> None:
        """df에 account_type/update_dt/reg_date/run_id/row_type='trade' 컬럼을 추가해 BigQuery에 WRITE_APPEND 적재.

        ARCH-003: run_id를 받아 모든 행에 동일하게 박는다 (sentinel marker와 같은 run_id 그룹).
        row_type='trade'는 run_marker와 구별하기 위한 명시적 라벨.
        """
        tmp = df.copy()
        now_kst = datetime.now(pytz.timezone('Asia/Seoul'))
        tmp['account_type'] = account_type
        tmp['update_dt'] = now_kst
        tmp['reg_date'] = now_kst.date()  # DATE 타입, 파티션 기준
        if run_id is not None:
            tmp['run_id'] = run_id
        tmp['row_type'] = 'trade'

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            # ARCH-004: 새 컬럼(requested_quantity, filled_quantity 등)이 자동 추가되도록 허용.
            # ARCH-003: row_type, run_id, run_status도 첫 적재 시 자동 컬럼 생성.
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
            schema=[
                bigquery.SchemaField('update_dt', 'TIMESTAMP'),
                bigquery.SchemaField('reg_date', 'DATE'),
            ],
            time_partitioning=bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field='reg_date',
            ),
        )

        job = self.client.load_table_from_dataframe(tmp, self._table_ref, job_config=job_config)
        job.result()
        logger.info('trade_log append 완료: %d rows → %s', len(tmp), self._table_ref)

    def append_run_marker(self, run_id: str, account_type: str, status: str) -> None:
        """주문 시작/완료 sentinel을 trade_log에 1행 적재 (ARCH-003).

        is_already_executed가 trade row가 없어도 run_marker만으로 중복 실행을 인지하도록.
        예: 'started' 마커 적재 후 주문 도중 크래시 → 다음 실행은 marker로 detect하여 skip.

        Args:
            run_id (str): main.py에서 발급한 실행 단위 식별자 (uuid).
            account_type (str): 계좌 식별자.
            status (str): 'started' 또는 'completed'.
        """
        now_kst = datetime.now(pytz.timezone('Asia/Seoul'))
        df = pd.DataFrame([{
            'run_id': run_id,
            'row_type': 'run_marker',
            'run_status': status,
            'account_type': account_type,
            'update_dt': now_kst,
            'reg_date': now_kst.date(),
        }])
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
            schema=[
                bigquery.SchemaField('update_dt', 'TIMESTAMP'),
                bigquery.SchemaField('reg_date', 'DATE'),
            ],
            time_partitioning=bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field='reg_date',
            ),
        )
        job = self.client.load_table_from_dataframe(df, self._table_ref, job_config=job_config)
        job.result()
        logger.info('run_marker append: run_id=%s, status=%s, account_type=%s', run_id, status, account_type)

    def is_already_executed(self, account_type: str, target_date, within_days: int = 7) -> bool:
        """trade_log 테이블에서 account_type 기준으로 within_days 이내 실행 여부를 조회.

        ARCH-003: row_type 필터 추가. trade row 또는 run_marker(started/completed)
        둘 중 하나만 있어도 "실행됨"으로 인지한다 (진행 중인 run도 중복 실행 방지).
        row_type IS NULL 허용은 마이그레이션 전 기존 행 호환용.
        """
        cutoff = (
            datetime.combine(target_date, datetime.min.time()) - timedelta(days=within_days - 1)
            if hasattr(target_date, 'year')
            else target_date - timedelta(days=within_days - 1)
        )
        cutoff_str = cutoff.strftime('%Y-%m-%d') if hasattr(cutoff, 'strftime') else str(cutoff)

        query = f"""
            SELECT COUNT(*) AS cnt
            FROM `{self._table_ref}`
            WHERE account_type = @account_type
              AND DATE(update_dt) >= @cutoff_date
              AND (row_type IN ('run_marker','trade') OR row_type IS NULL)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter('account_type', 'STRING', account_type),
                bigquery.ScalarQueryParameter('cutoff_date', 'DATE', cutoff_str),
            ]
        )

        try:
            result = self.client.query(query, job_config=job_config).result()
            row = next(iter(result))
            executed = row.cnt > 0
            logger.info(
                'is_already_executed(%s, within=%d days): %s (count=%d)',
                account_type, within_days, executed, row.cnt,
            )
            return executed
        except NotFound:
            # 테이블 미존재 = 첫 실행
            logger.info('trade_log 테이블 미존재, 첫 실행으로 간주: %s', self._table_ref)
            return False
