import argparse
from datetime import datetime
import pytz

from src.logger import get_logger
from src.config.env import GOOGLE_SHEET_URL
from src.static_allocation import StaticAllocationAgent
from src.pre_execute import is_executable

logger = get_logger(__name__)
kst = pytz.timezone('Asia/Seoul')
kst_date = datetime.now(kst).date()

parser = argparse.ArgumentParser()
parser.add_argument('--account_type', type=str)
args = parser.parse_args()

if is_executable(target_date=kst_date, account=args.account_type):
    obj = StaticAllocationAgent(account_type=args.account_type, gs_url=GOOGLE_SHEET_URL)
    obj.run()