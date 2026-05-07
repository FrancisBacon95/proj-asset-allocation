import time
import pandas as pd

from src.kis.client import KISClient
from src.logger import get_logger, log_method_call
from src.slack.client import slack_notify
logger = get_logger(__name__)

# sell 완료 후 buy를 시작하기 전 대기 시간 (초). 매도 체결 후 예수금 반영까지 시간이 필요하다.
SELL_TO_BUY_WAIT_SECONDS = 3
BUFFER_CASH = 10_000  # 리밸런싱 후 최소 예수금 버퍼 (원)

def _format_stock_header(row) -> str:
    return (
        f"*[{row['stock_nm']}: `{row['ticker']}`]*\n"
        f"- current_price: `{row['current_price']:,.0f}`\n"
        f"- current_quantity: `{row['current_quantity']:,.0f}`\n"
        f"- current_value: `{row['current_value']:,.0f}` (`{row['current_pct']:.2f}%` → 목표: `{row['weight']*100:.2f}%`)"
    )


def _format_plan_for_slack(df: pd.DataFrame) -> str:
    """리밸런싱 플랜 DataFrame을 Slack 메시지 형식의 문자열로 변환한다."""
    lines = []
    for _, row in df.iterrows():
        lines.append(
            _format_stock_header(row) + "\n"
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
            _format_stock_header(row) + "\n"
            f"- required_transaction: `{row['required_transaction']}`\n"
            f"- required_quantity: `{row['required_quantity']}`\n"
            f"- enable_quantity: `{row['enable_quantity']}`\n"
            f"- transaction_quantity: `{row['transaction_quantity']}`\n"
            f"- skipped_reason: `{row.get('skipped_reason')}`\n"
            f"- is_success: `{row['is_success']}`\n"
            f"- response_msg: `{row['response_msg']}`"
        )
    return '\n\n'.join(lines)


class PortfolioPlanner:
    """목표 비중과 현재 잔고를 바탕으로 리밸런싱 플랜을 계산한다."""

    def __init__(self, kis_client: KISClient, allocation_info: pd.DataFrame, account_type: str) -> None:
        self.kis_client = kis_client
        self.allocation_info = allocation_info
        self.account_type = account_type
        self._validate_allocation(allocation_info)

    @staticmethod
    def _validate_allocation(df: pd.DataFrame) -> None:
        if df.empty:
            raise ValueError("allocation_info가 비어 있습니다.")
        if df['ticker'].isnull().any() or (df['ticker'] == '').any():
            raise ValueError("ticker에 빈 값이 있습니다.")
        if df['ticker'].duplicated().any():
            dupes = df.loc[df['ticker'].duplicated(keep=False), 'ticker'].tolist()
            raise ValueError(f"ticker 중복이 있습니다: {dupes}")
        if (df['weight'] < 0).any():
            raise ValueError("weight에 음수 값이 있습니다.")

    @log_method_call
    def _create_total_info(self, allocation: pd.DataFrame, balance: pd.DataFrame) -> pd.DataFrame:
        """목표 비중과 현재 잔고를 합산해 종목별 거래 계획을 계산한다.

        총 잔고 금액을 기준으로 목표 금액(target_value)을 산출하고,
        현재 금액과의 차이로 필요 거래 수량과 방향(buy/sell)을 결정한다.
        """
        total_balance_value = balance['current_value'].sum()
        if total_balance_value <= BUFFER_CASH:
            raise ValueError(
                f"총 평가금액({total_balance_value:,.0f}원)이 버퍼({BUFFER_CASH:,}원) 이하입니다. 리밸런싱 중단."
            )
        logger.info('총 평가금액 (예수금 포함): %s원', f'{total_balance_value:,.0f}')
        slack_notify(f'[{self.account_type}] 총 평가금액 (예수금 포함)', f'`{total_balance_value:,.0f}원`')

        # 목표 비중 테이블에 현재 잔고를 left join. 미보유 종목은 current_quantity/value를 0으로 채운다.
        result = pd.merge(left=allocation, right=balance, on=['ticker'], how='left')
        result[['current_quantity', 'current_value']] = result[['current_quantity', 'current_value']].fillna(0)

        invalid_prices = result[~(result['current_price'] > 0)]
        if not invalid_prices.empty:
            raise ValueError(f"유효하지 않은 가격(0 또는 NaN)이 있는 종목: {invalid_prices['ticker'].tolist()}")

        # 리밸런싱 후 최소 예수금 버퍼 확보를 위해 10,000원을 차감한 금액을 기준으로 목표 금액을 계산한다.
        result['target_value'] = result['weight'] * int(total_balance_value - BUFFER_CASH)

        result['required_value'] = result['target_value'] - result['current_value']
        # 수량은 절댓값으로 계산 (방향은 required_transaction으로 별도 표현)
        result['required_quantity'] = abs((result['required_value'] / result['current_price']).astype(int))
        result['required_transaction'] = result['required_value'].apply(lambda x: 'buy' if x > 0 else 'sell' if x < 0 else None)
        result['current_pct'] = result['current_value'] / total_balance_value * 100
        return result

    @log_method_call
    def get_rebalancing_plan(self) -> pd.DataFrame:
        """현재 잔고와 목표 비중을 바탕으로 리밸런싱 플랜 DataFrame을 생성한다.

        required_value 기준 오름차순 정렬로 sell이 앞에 오도록 한다.
        (음수 required_value = sell, 양수 = buy)
        """
        allocation_info = self.allocation_info.copy()
        allocation_info['current_price'] = allocation_info['ticker'].apply(self.kis_client.fetch_price)

        full_balance = self.kis_client.fetch_domestic_total_balance()

        # stock_nm, current_price는 allocation_info 기준을 사용하므로 잔고에서 제거
        balance_info = full_balance.drop(columns=['stock_nm', 'current_price'])

        result = self._create_total_info(allocation=allocation_info, balance=balance_info)

        # allocation에 없는 종목(CASH 제외)은 전량 매도 대상으로 추가
        unallocated = full_balance[
            ~full_balance['ticker'].isin(allocation_info['ticker']) &
            (full_balance['ticker'] != 'CASH')
        ].copy()
        if not unallocated.empty:
            logger.info('allocation 미등록 종목 전량 매도 대상: %s', unallocated['ticker'].tolist())
            total_balance = full_balance['current_value'].sum()
            unallocated['category_1'] = None
            unallocated['category_2'] = None
            unallocated['weight'] = 0.0
            unallocated['target_value'] = 0.0
            unallocated['required_value'] = -unallocated['current_value']
            unallocated['required_quantity'] = unallocated['current_quantity'].astype(int)
            unallocated['required_transaction'] = 'sell'
            unallocated['current_pct'] = unallocated['current_value'] / total_balance * 100
            result = pd.concat([result, unallocated[result.columns]], ignore_index=True)

        result = result.sort_values(by='required_value', ascending=True).reset_index(drop=True)
        return result


class OrderExecutor:
    """리밸런싱 플랜에 따라 매도 → 매수 순서로 주문을 실행한다."""

    def __init__(self, kis_client: KISClient, account_type: str, is_test: bool = False) -> None:
        self.kis_client = kis_client
        self.account_type = account_type
        self.is_test = is_test

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

        # dnca_tot_amt는 CMA 등 즉시 주문 불가 금액을 포함할 수 있어 API의 nrcvb_buy_amt 기준으로 추적한다.
        if not buys.empty:
            first_ticker = buys.loc[buys.index[0]]['ticker']
            remaining_cash = self.kis_client.fetch_buy_orderable_cash(first_ticker)
        else:
            remaining_cash = cash_before_buy
        for i in buys.index:
            order_result = self._execute_order(buys.loc[i].to_dict(), i, available_cash=remaining_cash)
            # 체결 여부와 무관하게 API 반영 지연을 고려해 주문 금액을 즉시 차감
            remaining_cash -= order_result['transaction_quantity'] * int(buys.loc[i]['current_price'])
            result.append(order_result)
        cash_after_buy = self.kis_client.fetch_domestic_cash_balance()
        logger.info('buy 완료 후 예수금: %s', cash_after_buy)
        slack_notify(f'[{self.account_type}] buy 완료 후 예수금', f'`{cash_after_buy:,}원`')
        return pd.DataFrame(result)

    def _execute_order(self, plan_row: dict, order_index: int, available_cash: int = None) -> dict:
        """단일 종목의 주문을 실행하고 결과를 dict로 반환한다.

        실제 주문 수량은 계획 수량과 주문 가능 수량 중 작은 값을 사용한다.
        is_test=True이거나 주문 가능 수량이 0이면 API를 호출하지 않는다.
        """
        enable_qty = self._get_orderable_qty(ticker=plan_row['ticker'], transaction_type=plan_row['required_transaction'], available_cash=available_cash)
        # 계획 수량이 실제 가능 수량을 초과할 수 있으므로 min으로 제한
        transaction_qty = min(plan_row['required_quantity'], enable_qty)

        if self.is_test:
            skipped_reason = 'test_mode'
            response = None
        elif transaction_qty == 0:
            skipped_reason = 'zero_quantity'
            response = None
        else:
            skipped_reason = None
            response = self.kis_client.create_domestic_order(
                plan_row['required_transaction'], plan_row['ticker'],
                ord_qty=transaction_qty, ord_dvsn='01',
            )

        return {
            'ticker': plan_row['ticker'],
            'enable_quantity': enable_qty,
            'transaction_quantity': transaction_qty,
            'skipped_reason': skipped_reason,
            'is_success': response['rt_cd'] == '0' if response else None,  # KIS API 성공 코드는 '0'
            'response_msg': response['msg1'] if response else None,
            'transaction_order': order_index,  # 실행 순서 (Google Sheets 기록용)
        }

    def _get_orderable_qty(self, ticker: str, transaction_type: str, available_cash: int = None) -> int:
        """매수 또는 매도 가능 수량을 조회한다.

        매수의 경우:
        - available_cash가 전달되면 해당 값을 기준으로 수량을 계산한다 (연속 매수 시 잔여 현금 추적용).
        - 전달되지 않으면 API의 nrcvb_buy_amt + ruse_psbl_amt를 사용한다.
        dnca_tot_amt는 CMA 등 즉시 주문 불가 금액을 포함할 수 있어 사용하지 않는다.
        """
        if transaction_type == 'buy':
            result = self.kis_client.fetch_domestic_enable_buy(ticker=ticker, ord_dvsn='01')
            calc_price = int(result['psbl_qty_calc_unpr'])
            if available_cash is not None:
                cash_balance = available_cash
            else:
                # available_cash 없이 단독 호출 시 API의 실제 주문 가능 금액을 사용
                cash_balance = int(result['nrcvb_buy_amt']) + int(result['ruse_psbl_amt'])
            available_amt = cash_balance * 0.99
            logger.info(
                '[%s] cash_balance=%s, available_amt=%s, calc_price=%s → enable_qty=%s',
                ticker, f'{cash_balance:,}', f'{available_amt:,.0f}', f'{calc_price:,}',
                int(available_amt / calc_price) if calc_price > 0 else 0,
            )
            return int(available_amt / calc_price) if calc_price > 0 else 0
        elif transaction_type == 'sell':
            return int(self.kis_client.fetch_domestic_enable_sell(ticker=ticker)['ord_psbl_qty'])
        raise ValueError(f"transaction_type must be 'buy' or 'sell', got: {transaction_type!r}")


class StaticAllocator:
    """목표 비중 기반 국내주식 정적 자산배분 실행기.

    allocation_info(종목별 목표 비중)와 현재 잔고를 비교해
    매수/매도 수량을 계산하고 주문을 실행한다.
    """
    @log_method_call
    def __init__(self, account_type: str, allocation_info: pd.DataFrame, is_test: bool = False) -> None:
        self.account_type = account_type
        self.allocation_info = allocation_info
        self.is_test = is_test
        kis_client = KISClient(account_type)
        self.planner = PortfolioPlanner(kis_client, allocation_info, account_type)
        self.executor = OrderExecutor(kis_client, account_type, is_test)

    @log_method_call
    def run(self) -> pd.DataFrame:
        """리밸런싱 전체 플로우를 실행하고 플랜 + 실행 결과를 합산한 DataFrame을 반환한다."""
        total_info = self.planner.get_rebalancing_plan()
        logger.info('total_info:\n%s', total_info.to_string())
        slack_notify(f'[{self.account_type}] 리밸런싱 플랜', _format_plan_for_slack(total_info))
        trade_log = self.executor.run_rebalancing(plan_df=total_info)
        # 플랜(total_info)과 실행 결과(trade_log)를 ticker 기준으로 합산해 반환
        return total_info.merge(trade_log, on='ticker', how='outer')
