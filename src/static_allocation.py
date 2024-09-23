import pytz
import pandas as pd
from datetime import datetime

from src.core.kis_agent import KISAgent
from src.auth.gcp_auth import GCPAuth
from src.logger import get_logger
from src.config.helper import log_method_call
logger = get_logger(__name__)

class StaticAllocationAgent():
    @log_method_call
    def __init__(self, account_type: str, gs_url: str) -> None:
        self.account_type = account_type
        self.gs_url = gs_url
        self.kis_agent = KISAgent(self.account_type)
        self.gs_auth = GCPAuth(url=self.gs_url)
        self.gs_sheet = f'{self.account_type}_allocation'
        self.allocation_info = self.get_allocation_info()
    
    @log_method_call
    def get_allocation_info(self) -> pd.DataFrame:
        result = self.gs_auth.get_df_from_google_sheets(sheet=self.gs_sheet)
        result['ticker'] = result['ticker'].astype(str)
        result['weight'] = result['weight'].astype(float)
        return result
    
    @log_method_call
    def create_total_info(self, allocation: pd.DataFrame, balance: pd.DataFrame) -> pd.DataFrame:
        total_balance_value = balance['current_value'].sum()
        logger.info('total_balance_value: %s', total_balance_value)

        result = pd.merge(left=allocation, right=balance, on=['ticker'], how='left')
        result[['current_quantity', 'current_value']] = result[['current_quantity', 'current_value']].fillna(0)
        result['target_value'] = result['weight'] * int(total_balance_value)

        result['required_value'] = result['target_value'] - result['current_value']
        result['required_quantity'] = (result['required_value'] / result['current_price']).astype(int)
        return result
    
    @log_method_call
    def run_asset_allocation(self, total_info: pd.DataFrame) -> pd.DataFrame:
        result = []
        for i in total_info.index:
            tmp = total_info.loc[i].to_dict()
            if   tmp['required_quantity'] < 0:
                transaction_type='sell'
            elif tmp['required_quantity'] > 0:
                transaction_type='buy'
            else:
                continue
            
            response = self.kis_agent.create_domestic_order(transaction_type, tmp['ticker'], ord_qty=tmp['required_quantity'], ord_dvsn='01')
            is_success = True if response['rt_cd'] == str(0) else False
            tmp = {
                'ticker': tmp['ticker'], 
                'quantity': tmp['required_quantity'], 
                'transaction_type': transaction_type.upper(), 
                'is_success': is_success
            }
            logger.info(tmp)
            result += [tmp]
        return pd.DataFrame(result)
        
    @log_method_call
    def run(self):
        # 자산 배분 비중 정보 가져오기
        allocation_info = self.allocation_info.copy()
        # 현재 가격 붙이기
        allocation_info['current_price'] = allocation_info['ticker'].apply(self.kis_agent.fetch_price)

        # 현재 잔고 불러오기
        balance_info =self.kis_agent.fetch_domestic_total_balance().drop(columns=['stock_nm', 'current_price'])
        
        # 현재 잔고에 대한 자산분배 정보 만들기
        total_info = self.create_total_info(allocation=allocation_info, balance=balance_info)
        
        trade_log = self.run_asset_allocation(total_info=total_info)
        self.gs_auth.write_worksheet(trade_log, f'{self.account_type}_trad_log')