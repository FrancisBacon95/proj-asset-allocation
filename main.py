import argparse
from datetime import datetime
import pytz
from dotenv import load_dotenv
load_dotenv()

from src.logger import get_logger
from src.config.env import GOOGLE_SHEET_URL
from src.sheets.client import GoogleSheetsClient
from src.bigquery.client import BigQueryClient
from src.allocation import StaticAllocator
from src.slack.client import slack_notify, format_rebalancing_summary, format_irp_plan_summary

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--account_type', type=str, required=True)
    parser.add_argument('--test', action='store_true', default=False)
    parser.add_argument('--force', action='store_true', default=False)
    return parser.parse_args()


def main() -> None:
    kst = pytz.timezone('Asia/Seoul')
    kst_now = datetime.now(kst)
    kst_date = kst_now.date()
    args = _parse_args()

    gs_client = GoogleSheetsClient(url=GOOGLE_SHEET_URL)
    bq_client = None if args.test else BigQueryClient()

    allocation_info = gs_client.get_df_from_google_sheets(f'{args.account_type}_allocation')
    allocation_info['ticker'] = allocation_info['ticker'].astype(str)
    allocation_info['weight'] = allocation_info['weight'].astype(float)

    allocator = StaticAllocator(account_type=args.account_type, allocation_info=allocation_info, is_test=args.test)
    is_irp = allocator.planner.kis_client.is_irp()

    is_market_open = allocator.is_trading_day(kst_date)
    # IRP는 BigQuery에 거래 이력을 적재하지 않아 is_already_executed가 항상 False를
    # 반환한다. 무한 재실행을 막는 진짜 빈도 제어는 외부 cron(예: Cloud Run 매달 1일)에 맡긴다.
    if args.test or is_irp:
        already_executed = False
        if is_irp:
            logger.info('IRP 계좌: is_already_executed 체크 스킵 (BQ 거래 이력 부재). cron으로 빈도 제어.')
    else:
        already_executed = bq_client.is_already_executed(args.account_type, kst_date)

    logger.info('is_market_open: %s', is_market_open)
    logger.info('is_already_executed: %s', already_executed)

    if args.test or args.force or (is_market_open and not already_executed):
        result, remaining_cash = allocator.run()

        if is_irp:
            # IRP: 플랜만 생성됨. Sheets에 IRP_action_plan으로 덮어쓰기, BigQuery 적재 스킵.
            sheet_name = f'{args.account_type}_action_plan'
            gs_client.overwrite_dataframe(sheet_name, result)
            sheet_url = gs_client.get_worksheet_url(sheet_name)
            logger.info('IRP plan written to %s sheet', sheet_name)

            summary = format_irp_plan_summary(
                plan_df=result,
                account_type=args.account_type,
                dt=kst_now,
                sheet_url=sheet_url,
            )
            slack_notify(f'[{args.account_type}] IRP 리밸런싱 플랜 생성', summary)
        else:
            trade_log_url = gs_client.get_worksheet_url(f'{args.account_type}_trade_log')
            summary = format_rebalancing_summary(
                result_df=result,
                remaining_cash=remaining_cash,
                account_type=args.account_type,
                dt=kst_now,
                trade_log_url=trade_log_url,
            )
            slack_notify(f'[{args.account_type}] 리밸런싱 완료', summary)

            if bq_client is not None:
                bq_client.append_trade_log(result, account_type=args.account_type)


if __name__ == '__main__':
    main()
