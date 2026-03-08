import time
import pandas as pd

from src.kis.client import KISClient
from src.logger import get_logger, log_method_call
logger = get_logger(__name__)

ORDER_INTERVAL_SECONDS = 3
SELL_TO_BUY_WAIT_SECONDS = 30

class StaticAllocator():
    @log_method_call
    def __init__(self, account_type: str, allocation_info: pd.DataFrame, is_test: bool = False) -> None:
        self.account_type = account_type
        self.kis_client = KISClient(self.account_type)
        self.allocation_info = allocation_info
        self.is_test = is_test

    @log_method_call
    def create_total_info(self, allocation: pd.DataFrame, balance: pd.DataFrame) -> pd.DataFrame:
        total_balance_value = balance['current_value'].sum()
        logger.info('total_balance_value: %s', total_balance_value)

        result = pd.merge(left=allocation, right=balance, on=['ticker'], how='left')
        result[['current_quantity', 'current_value']] = result[['current_quantity', 'current_value']].fillna(0)
        result['target_value'] = result['weight'] * int(total_balance_value)

        result['required_value'] = result['target_value'] - result['current_value']
        result['required_quantity'] = abs((result['required_value'] / result['current_price']).astype(int))
        result['required_transaction'] = result['required_value'].apply(lambda x: 'buy' if x > 0 else 'sell' if x < 0 else None)
        return result

    @log_method_call
    def run_rebalancing(self, plan_df: pd.DataFrame) -> pd.DataFrame:
        sells = plan_df[plan_df['required_transaction'] == 'sell']
        buys = plan_df[plan_df['required_transaction'] == 'buy']

        result = []
        for i in sells.index:
            result += [self._execute_order(sells.loc[i].to_dict(), i)]
            time.sleep(ORDER_INTERVAL_SECONDS)

        if sells.shape[0] > 0 and buys.shape[0] > 0:
            logger.info('sell 완료. %s초 대기 후 buy 시작.', SELL_TO_BUY_WAIT_SECONDS)
            time.sleep(SELL_TO_BUY_WAIT_SECONDS)

        for i in buys.index:
            result += [self._execute_order(buys.loc[i].to_dict(), i)]
            time.sleep(ORDER_INTERVAL_SECONDS)

        return pd.DataFrame(result)

    def _execute_order(self, tmp: dict, order_index: int) -> dict:
        tmp['enable_quantity'] = self.get_enable_qty(ticker=tmp['ticker'], transaction_type=tmp['required_transaction'])
        transaction_qty = min(tmp['required_quantity'], tmp['enable_quantity'])

        if self.is_test is True:
            response = {'msg1': 'TEST', 'rt_cd': '99'}
        elif transaction_qty == 0:
            response = {'msg1': '거래 가능 수량이 없습니다.', 'rt_cd': '99'}
        else:
            response = self.kis_client.create_domestic_order(tmp['required_transaction'], tmp['ticker'], ord_qty=transaction_qty, ord_dvsn='01')

        is_success = True if response['rt_cd'] == str(0) else False
        return {
            'ticker': tmp['ticker'],
            'enable_quantity': tmp['enable_quantity'],
            'transaction_quantity': transaction_qty,
            'is_success': is_success,
            'response_msg': response['msg1'],
            'transaction_order': order_index,
        }

    def get_enable_qty(self, ticker: str, transaction_type: str) -> int:
        if transaction_type == 'buy':
            return int(self.kis_client.fetch_domestic_enable_buy(ticker=ticker, ord_dvsn='01')['nrcvb_buy_qty'])
        elif transaction_type == 'sell':
            return int(self.kis_client.fetch_domestic_enable_sell(ticker=ticker)['ord_psbl_qty'])
        raise ValueError(f"transaction_type must be 'buy' or 'sell', got: {transaction_type!r}")

    def get_rebalancing_plan(self) -> pd.DataFrame:
        allocation_info = self.allocation_info.copy()
        allocation_info['current_price'] = allocation_info['ticker'].apply(self.kis_client.fetch_price)

        balance_info = self.kis_client.fetch_domestic_total_balance().drop(columns=['stock_nm', 'current_price'])

        result = self.create_total_info(allocation=allocation_info, balance=balance_info)
        result = result.sort_values(by='required_value', ascending=True).reset_index(drop=True)
        return result

    @log_method_call
    def run(self) -> pd.DataFrame:
        total_info = self.get_rebalancing_plan()
        trade_log = self.run_rebalancing(plan_df=total_info)
        return total_info.merge(trade_log, on='ticker', how='outer')
