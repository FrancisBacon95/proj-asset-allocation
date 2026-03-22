import time
import pandas as pd

from src.kis.client import KISClient
from src.logger import get_logger, log_method_call
from src.slack.client import slack_notify
logger = get_logger(__name__)

# sell 완료 후 buy를 시작하기 전 대기 시간 (초). 매도 체결 후 예수금 반영까지 시간이 필요하다.
SELL_TO_BUY_WAIT_SECONDS = 3


def _format_plan_for_slack(df: pd.DataFrame) -> str:
    """리밸런싱 플랜 DataFrame을 Slack 메시지 형식의 문자열로 변환한다."""
    lines = []
    for _, row in df.iterrows():
        lines.append(
            f"*[{row['stock_nm']}: `{row['ticker']}`]*\n"
            f"- current_price: `{row['current_price']:,.0f}`\n"
            f"- current_quantity: `{row['current_quantity']:,.0f}`\n"
            f"- target_value: `{row['target_value']:,.0f}`\n"
            f"- required_value: `{row['required_value']:,.0f}`\n"
            f"- required_quantity: `{row['required_quantity']}`\n"
            f"- required_transaction: `{row['required_transaction']}`"
        )
    return '\n\n'.join(lines)


def _format_result_for_slack(df: pd.DataFrame) -> str:
    """리밸런싱 실행 결과 DataFrame을 Slack 메시지 형식의 문자열로 변환한다."""
    lines = []
    for _, row in df.iterrows():
        lines.append(
            f"*[{row['stock_nm']}: `{row['ticker']}`]*\n"
            f"- required_transaction: `{row['required_transaction']}`\n"
            f"- required_quantity: `{row['required_quantity']}`\n"
            f"- enable_quantity: `{row['enable_quantity']}`\n"
            f"- transaction_quantity: `{row['transaction_quantity']}`\n"
            f"- is_success: `{row['is_success']}`\n"
            f"- response_msg: `{row['response_msg']}`"
        )
    return '\n\n'.join(lines)


class StaticAllocator():
    """목표 비중 기반 국내주식 정적 자산배분 실행기.

    allocation_info(종목별 목표 비중)와 현재 잔고를 비교해
    매수/매도 수량을 계산하고 주문을 실행한다.
    """
    @log_method_call
    def __init__(self, account_type: str, allocation_info: pd.DataFrame, is_test: bool = False) -> None:
        self.account_type = account_type
        self.kis_client = KISClient(self.account_type)
        self.allocation_info = allocation_info
        # is_test=True이면 주문 API를 호출하지 않고 더미 응답을 반환한다
        self.is_test = is_test

    @log_method_call
    def _create_total_info(self, allocation: pd.DataFrame, balance: pd.DataFrame) -> pd.DataFrame:
        """목표 비중과 현재 잔고를 합산해 종목별 거래 계획을 계산한다.

        총 잔고 금액을 기준으로 목표 금액(target_value)을 산출하고,
        현재 금액과의 차이로 필요 거래 수량과 방향(buy/sell)을 결정한다.
        """
        total_balance_value = balance['current_value'].sum()
        logger.info('total_balance_value: %s', total_balance_value)

        # 목표 비중 테이블에 현재 잔고를 left join. 미보유 종목은 current_quantity/value를 0으로 채운다.
        result = pd.merge(left=allocation, right=balance, on=['ticker'], how='left')
        result[['current_quantity', 'current_value']] = result[['current_quantity', 'current_value']].fillna(0)
        result['target_value'] = result['weight'] * int(total_balance_value)

        result['required_value'] = result['target_value'] - result['current_value']
        # 수량은 절댓값으로 계산 (방향은 required_transaction으로 별도 표현)
        result['required_quantity'] = abs((result['required_value'] / result['current_price']).astype(int))
        result['required_transaction'] = result['required_value'].apply(lambda x: 'buy' if x > 0 else 'sell' if x < 0 else None)
        return result

    @log_method_call
    def run_rebalancing(self, plan_df: pd.DataFrame) -> pd.DataFrame:
        """리밸런싱 플랜에 따라 매도 → 매수 순서로 주문을 실행한다.

        매도를 먼저 실행해 예수금을 확보한 뒤 매수를 진행한다.
        sell/buy 사이에는 체결 후 예수금 반영 대기를 위해 sleep한다.
        """
        sells = plan_df[plan_df['required_transaction'] == 'sell']
        buys = plan_df[plan_df['required_transaction'] == 'buy']

        cash_before_sell = self.kis_client.fetch_domestic_cash_balance()
        logger.info('sell 시작 전 예수금: %s', cash_before_sell)
        slack_notify(f'[{self.account_type}] sell 시작 전 예수금', f'`{cash_before_sell:,}원`')

        result = []
        for i in sells.index:
            result.append(self._execute_order(sells.loc[i].to_dict(), i))

        # sell이 하나라도 있었고 buy도 있을 때만 대기. sell-only 또는 buy-only 시나리오는 skip.
        if sells.shape[0] > 0 and buys.shape[0] > 0:
            logger.info('sell 완료. %s초 대기 후 buy 시작.', SELL_TO_BUY_WAIT_SECONDS)
            time.sleep(SELL_TO_BUY_WAIT_SECONDS)

        cash_before_buy = self.kis_client.fetch_domestic_cash_balance()
        logger.info('buy 시작 전 예수금: %s', cash_before_buy)
        slack_notify(f'[{self.account_type}] sell 이후 예수금', f'`{cash_before_buy:,}원`')

        for i in buys.index:
            result.append(self._execute_order(buys.loc[i].to_dict(), i))

        cash_after_buy = self.kis_client.fetch_domestic_cash_balance()
        logger.info('buy 완료 후 예수금: %s', cash_after_buy)
        slack_notify(f'[{self.account_type}] buy 완료 후 예수금', f'`{cash_after_buy:,}원`')
        return pd.DataFrame(result)

    def _execute_order(self, plan_row: dict, order_index: int) -> dict:
        """단일 종목의 주문을 실행하고 결과를 dict로 반환한다.

        실제 주문 수량은 계획 수량과 주문 가능 수량 중 작은 값을 사용한다.
        is_test=True이거나 주문 가능 수량이 0이면 API를 호출하지 않는다.
        """
        enable_qty = self._get_orderable_qty(ticker=plan_row['ticker'], transaction_type=plan_row['required_transaction'])
        # 계획 수량이 실제 가능 수량을 초과할 수 있으므로 min으로 제한
        transaction_qty = min(plan_row['required_quantity'], enable_qty)

        if self.is_test is True:
            response = {'msg1': 'TEST', 'rt_cd': '99'}
        elif transaction_qty == 0:
            response = {'msg1': '거래 가능 수량이 없습니다.', 'rt_cd': '99'}
        else:
            response = self.kis_client.create_domestic_order(plan_row['required_transaction'], plan_row['ticker'], ord_qty=transaction_qty, ord_dvsn='01')

        return {
            'ticker': plan_row['ticker'],
            'enable_quantity': enable_qty,
            'transaction_quantity': transaction_qty,
            'is_success': response['rt_cd'] == '0',  # KIS API 성공 코드는 '0'
            'response_msg': response['msg1'],
            'transaction_order': order_index,  # 실행 순서 (Google Sheets 기록용)
        }

    def _get_orderable_qty(self, ticker: str, transaction_type: str) -> int:
        """매수 또는 매도 가능 수량을 조회한다.

        매수의 경우 예수금 기준으로 살 수 있는 수량을 계산한다.
        psbl_qty_calc_unpr: 수량 계산에 사용되는 단가
        nrcvb_buy_amt: 미수 없는 매수 가능 금액
        ruse_psbl_amt: 재사용 가능 금액
        """
        if transaction_type == 'buy':
            result = self.kis_client.fetch_domestic_enable_buy(ticker=ticker, ord_dvsn='01')
            calc_price = int(result['psbl_qty_calc_unpr'])
            available_amt = (int(result['nrcvb_buy_amt']) + int(result['ruse_psbl_amt'])) * 0.99
            return int(available_amt / calc_price) if calc_price > 0 else 0
        elif transaction_type == 'sell':
            return int(self.kis_client.fetch_domestic_enable_sell(ticker=ticker)['ord_psbl_qty'])
        raise ValueError(f"transaction_type must be 'buy' or 'sell', got: {transaction_type!r}")

    def get_rebalancing_plan(self) -> pd.DataFrame:
        """현재 잔고와 목표 비중을 바탕으로 리밸런싱 플랜 DataFrame을 생성한다.

        required_value 기준 오름차순 정렬로 sell이 앞에 오도록 한다.
        (음수 required_value = sell, 양수 = buy)
        """
        allocation_info = self.allocation_info.copy()
        allocation_info['current_price'] = allocation_info['ticker'].apply(self.kis_client.fetch_price)

        # stock_nm, current_price는 allocation_info 기준을 사용하므로 잔고에서 제거
        balance_info = self.kis_client.fetch_domestic_total_balance().drop(columns=['stock_nm', 'current_price'])

        result = self._create_total_info(allocation=allocation_info, balance=balance_info)
        result = result.sort_values(by='required_value', ascending=True).reset_index(drop=True)
        return result

    @log_method_call
    def run(self) -> pd.DataFrame:
        """리밸런싱 전체 플로우를 실행하고 플랜 + 실행 결과를 합산한 DataFrame을 반환한다."""
        total_info = self.get_rebalancing_plan()
        logger.info('total_info:\n%s', total_info.to_string())
        slack_notify(f'[{self.account_type}] 리밸런싱 플랜', _format_plan_for_slack(total_info))
        trade_log = self.run_rebalancing(plan_df=total_info)
        # 플랜(total_info)과 실행 결과(trade_log)를 ticker 기준으로 합산해 반환
        return total_info.merge(trade_log, on='ticker', how='outer')