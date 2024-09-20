from src.core.kis_agent import KISAgent
from src.auth.gcp_auth import GCPAuth
import pandas as pd
from src.logger import get_logger
from src.config.helper import log_method_call
logger = get_logger(__name__)

class StaticAllocationAgent():
    @log_method_call
    def __init__(self, account_type: str, gs_url: str) -> None:
        self.kis_agent = KISAgent(account_type)
        self.gs_url = gs_url
        self.gs_sheet = f'{account_type}_allocation'
        self.allocation_info = self.get_allocation_info()
    
    @log_method_call
    def get_allocation_info(self) -> pd.DataFrame:
        result = GCPAuth().get_df_from_google_sheets(url=self.gs_url,sheet=self.gs_sheet)
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
    def run_asset_allocation(self, total_info: pd.DataFrame) -> None:
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
        
        self.run_asset_allocation(total_info=total_info)