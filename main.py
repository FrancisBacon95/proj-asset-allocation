import argparse
from datetime import datetime
import pandas as pd
import pytz

from src.logger import get_logger
from src.config.env import GOOGLE_SHEET_URL
from src.static_allocation import StaticAllocationAgent

logger = get_logger(__name__)
kst = pytz.timezone('Asia/Seoul')
kst_date = datetime.now(kst).date()

parser = argparse.ArgumentParser()
parser.add_argument('--account_type', type=str)
args = parser.parse_args()

obj = StaticAllocationAgent(account_type=args.account_type, gs_url=GOOGLE_SHEET_URL)

is_market_open = obj.kis_agent.is_trading_day(kst_date)
latest_trade_date = pd.to_datetime(
    obj.gs_auth.get_df_from_google_sheets(f'{args.account_type}_trade_log')['update_dt']
).dt.date.unique()[0]
is_already_executed = (kst_date - latest_trade_date).days < 7

print(f'- is_market_open: {is_market_open}')
print(f'- is_already_executed: {is_already_executed}')

if is_market_open and not is_already_executed:
    total_info = obj.get_rebalancing_plan()

    # 자산 분배 및 거래 로그 수집
    trade_log = obj.run_rebalancing(plan_df=total_info)

    # 구글 시트 업로드
    result = total_info.merge(trade_log, on='ticker', how='outer')
    obj.gs_auth.write_worksheet(result, f'{obj.account_type}_trade_log')