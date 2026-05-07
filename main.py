import argparse
from datetime import datetime
import pandas as pd
import pytz

from src.logger import get_logger
from src.config.env import GOOGLE_SHEET_URL
from src.sheets.client import GoogleSheetsClient
from src.allocation import StaticAllocator, _format_result_for_slack
from src.slack.client import slack_notify

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--account_type', type=str, required=True)
    parser.add_argument('--test', action='store_true', default=False)
    parser.add_argument('--force', action='store_true', default=False)
    return parser.parse_args()


def _is_already_executed(gs_client: GoogleSheetsClient, account_type: str, kst_date) -> bool:
    trade_log_sheet = f'{account_type}_trade_log'
    worksheet_titles = [ws.title for ws in gs_client.spreadsheet.worksheets()]
    if trade_log_sheet not in worksheet_titles:
        return False
    trade_log_dates = pd.to_datetime(
        gs_client.get_df_from_google_sheets(trade_log_sheet)['update_dt']
    ).dt.date
    if trade_log_dates.empty:
        return False
    return (kst_date - trade_log_dates.max()).days < 7


def main() -> None:
    kst = pytz.timezone('Asia/Seoul')
    kst_date = datetime.now(kst).date()
    args = _parse_args()

    gs_client = GoogleSheetsClient(url=GOOGLE_SHEET_URL)
    allocation_info = gs_client.get_df_from_google_sheets(f'{args.account_type}_allocation')
    allocation_info['ticker'] = allocation_info['ticker'].astype(str)
    allocation_info['weight'] = allocation_info['weight'].astype(float)

    obj = StaticAllocator(account_type=args.account_type, allocation_info=allocation_info, is_test=args.test)

    is_market_open = obj.kis_client.is_trading_day(kst_date)
    already_executed = _is_already_executed(gs_client, args.account_type, kst_date)

    logger.info('is_market_open: %s', is_market_open)
    logger.info('is_already_executed: %s', already_executed)

    if args.test or args.force or (is_market_open and not already_executed):
        result = obj.run()
        slack_notify(f'[{args.account_type}] 리밸런싱 결과', _format_result_for_slack(result))
        if not args.test:
            gs_client.write_worksheet(result, f'{args.account_type}_trade_log')


if __name__ == '__main__':
    main()
