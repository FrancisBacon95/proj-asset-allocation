from src.core.kis_agent import KISAgent
from src.auth.gcp_auth import GCPAuth
import pandas as pd
from src.logger import get_logger
from src.config.helper import log_method_call
logger = get_logger(__name__)

class StaticAllocationAgent():
    @log_method_call
    def __init__(self) -> None:
        self.kis_agent = KISAgent('ISA')
        self.gs_url = 'https://docs.google.com/spreadsheets/d/1xEpqV1TaoB4lS-O5ZONCekf1-NZBhAmvDhorukU_kFQ'
        self.gs_sheet = 'allocation'
        self.allocation_info = self.get_allocation_info()
    
    @log_method_call
    def get_allocation_info(self) -> pd.DataFrame:
        result = GCPAuth().get_df_from_google_sheets(url=self.gs_url,sheet=self.gs_sheet)
        result['ticker'] = result['ticker'].astype(str)
        result['weight'] = result['weight'].astype(float)
        return result
    
    @log_method_call
    def run(self):
        # 현재가격 불러오기
        total_info = self.allocation_info.copy()
        total_info['current_price'] = total_info['ticker'].apply(self.kis_agent.fetch_price)

        total_balance =self.kis_agent.fetch_domestic_total_balance().drop(columns=['stock_nm', 'current_price'])
        total_balance_value = total_balance['current_value'].sum()
        total_info['target_value'] = total_info['weight'] * int(total_balance_value)

        total_info = total_info.merge(total_balance, on=['ticker'], how='left')
        total_info[['current_quantity', 'current_value']] = total_info[['current_quantity', 'current_value']].fillna(0)
        total_info['required_value'] = total_info['target_value'] - total_info['current_value']
        total_info['required_quantity'] = (total_info['required_value'] / total_info['current_price']).astype(int)

        logger.info('total_balance_value: %s', total_balance_value)

        for i in total_info.index:
            tmp = total_info.loc[i].to_dict()
            if   tmp['required_quantity'] < 0:
                response = self.kis_agent.sell_domestic_stock(tmp['ticker'], ord_qty=tmp['required_quantity'], ord_dvsn='01')
                logger.info(response)
            elif tmp['required_quantity'] > 0:
                response = self.kis_agent. buy_domestic_stock(tmp['ticker'], ord_qty=tmp['required_quantity'], ord_dvsn='01')
                logger.info(response)
            else:
                continue