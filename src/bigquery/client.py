"""BigQuery 거래 이력 적재 및 중복 실행 방지 (ARCH-003).

핵심 설계:
- run_id + row_type sentinel: 모든 trade row는 `run_id` + `row_type='trade'`로
  라벨. main.py가 주문 직전·후에 `append_run_marker()`로 `row_type='run_marker'`
  + `run_status='started'/'completed'` sentinel을 적재.
- `is_already_executed`: run_id 단위로 그룹핑한 후 다음 중 하나라도 만족하면
  차단 — (a) 정상 완료(trade 또는 completed marker), (b) 진행 중(started 후
  STALE_MINUTES=30분 미경과), (c) row_type IS NULL (마이그레이션 전 행).
- 좀비 자동 stale: started 후 30분 지났는데 completed/trade가 없으면 좀비로
  판정 → 다음 cron 자동 재시도 (개발자 수동 정리 불필요).
- 스키마 자동 확장: `LoadJobConfig.schema_update_options=ALLOW_FIELD_ADDITION`
  로 신규 컬럼(run_id/row_type/run_status/requested_quantity/filled_quantity)을
  첫 적재 시 BQ가 자동 추가 (ALTER 직접 실행 불필요).
"""
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

# 좀비 run 자동 stale 처리 임계 (ARCH-003 후속, Codex P0 #1+#2).
# started marker만 있고 STALE_MINUTES 지나도 completed/trade row가 없으면
# 진행 중이 아니라 좀비로 판정 → is_already_executed=False → 다음 cron 재시도 가능.
# 향후 ExecutionPolicy로 옮길 수 있게 모듈 상수로 보관.
STALE_MINUTES = 30


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

        ARCH-003 + Codex P0 #1·#2: run_id 단위로 그룹핑한 후 다음 중 하나라도 해당하면 차단:
        - (a) trade row 또는 completed marker가 있음 → 정상 완료 run
        - (b) started marker만 있고 STALE_MINUTES 안 지남 → 진행 중인 run (동시 실행 방지)
        - (c) row_type IS NULL인 옛날 행 → 마이그레이션 전 호환

        started 후 STALE_MINUTES(30분) 지났는데 completed/trade가 없으면 좀비 run으로
        판정하여 다음 cron이 재시도 가능 (개발자 수동 정리 불필요).
        """
        cutoff = (
            datetime.combine(target_date, datetime.min.time()) - timedelta(days=within_days - 1)
            if hasattr(target_date, 'year')
            else target_date - timedelta(days=within_days - 1)
        )
        cutoff_str = cutoff.strftime('%Y-%m-%d') if hasattr(cutoff, 'strftime') else str(cutoff)

        query = f"""
            WITH runs AS (
              SELECT
                run_id,
                MAX(IF(row_type='trade' OR run_status='completed', 1, 0)) AS has_terminal,
                MIN(IF(run_status='started', update_dt, NULL)) AS started_at
              FROM `{self._table_ref}`
              WHERE account_type = @account_type
                AND DATE(update_dt) >= @cutoff_date
                AND (row_type IN ('run_marker','trade') OR row_type IS NULL)
              GROUP BY run_id
            )
            SELECT COUNT(*) AS cnt
            FROM runs
            WHERE
              -- (a) 정상 완료: trade row 또는 completed marker
              has_terminal = 1
              -- (b) started 후 STALE_MINUTES 미경과 (진행 중)
              OR (has_terminal = 0
                  AND started_at IS NOT NULL
                  AND TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), started_at, MINUTE) < @stale_minutes)
              -- (c) 마이그레이션 전 옛날 행 (run_id NULL)
              OR run_id IS NULL
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter('account_type', 'STRING', account_type),
                bigquery.ScalarQueryParameter('cutoff_date', 'DATE', cutoff_str),
                bigquery.ScalarQueryParameter('stale_minutes', 'INT64', STALE_MINUTES),
            ]
        )

        try:
            result = self.client.query(query, job_config=job_config).result()
            row = next(iter(result))
            executed = row.cnt > 0
            logger.info(
                'is_already_executed(%s, within=%d days, stale=%dmin): %s (count=%d)',
                account_type, within_days, STALE_MINUTES, executed, row.cnt,
            )
            return executed
        except NotFound:
            # 테이블 미존재 = 첫 실행
            logger.info('trade_log 테이블 미존재, 첫 실행으로 간주: %s', self._table_ref)
            return False
