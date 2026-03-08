import argparse
from datetime import datetime
import pandas as pd
import pytz

from src.logger import get_logger
from src.config.env import GOOGLE_SHEET_URL
from src.auth.gcp_auth import GCPAuth
from src.static_allocation import StaticAllocationAgent

logger = get_logger(__name__)

if __name__ == '__main__':
    kst = pytz.timezone('Asia/Seoul')
    kst_date = datetime.now(kst).date()

    parser = argparse.ArgumentParser()
    parser.add_argument('--account_type', type=str)
    args = parser.parse_args()

    gs_auth = GCPAuth(url=GOOGLE_SHEET_URL)
    allocation_info = gs_auth.get_df_from_google_sheets(f'{args.account_type}_allocation')
    allocation_info['ticker'] = allocation_info['ticker'].astype(str)
    allocation_info['weight'] = allocation_info['weight'].astype(float)

    obj = StaticAllocationAgent(account_type=args.account_type, allocation_info=allocation_info)

    is_market_open = obj.kis_agent.is_trading_day(kst_date)
    latest_trade_date = pd.to_datetime(
        gs_auth.get_df_from_google_sheets(f'{args.account_type}_trade_log')['update_dt']
    ).dt.date.unique()[0]
    is_already_executed = (kst_date - latest_trade_date).days < 7

    print(f'- is_market_open: {is_market_open}')
    print(f'- is_already_executed: {is_already_executed}')

    if is_market_open and not is_already_executed:
        result = obj.run()
        gs_auth.write_worksheet(result, f'{args.account_type}_trade_log')
