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

    def append_trade_log(self, df: pd.DataFrame, account_type: str) -> None:
        """df에 account_type, update_dt(KST) 컬럼을 추가한 뒤 BigQuery에 WRITE_APPEND 모드로 적재."""
        tmp = df.copy()
        now_kst = datetime.now(pytz.timezone('Asia/Seoul'))
        tmp['account_type'] = account_type
        tmp['update_dt'] = now_kst
        tmp['reg_date'] = now_kst.date()  # DATE 타입, 파티션 기준

        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
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

    def is_already_executed(self, account_type: str, target_date, within_days: int = 7) -> bool:
        """trade_log 테이블에서 account_type 기준으로 within_days 이내 실행 여부를 조회."""
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
