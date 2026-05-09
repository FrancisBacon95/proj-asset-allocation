import time
from typing import Optional
import pandas as pd

from src.kis.client import KISClient
from src.logger import get_logger, log_method_call
from src.policy import DEFAULT_EXECUTION_POLICY, ExecutionPolicy

logger = get_logger(__name__)

# Deprecated: ExecutionPolicy.sell_to_buy_wait_seconds로 이동 (ARCH-007).
# 외부 import 호환을 위해 모듈 상수도 유지하되 본 모듈 내부에서는 사용하지 않는다.
SELL_TO_BUY_WAIT_SECONDS = DEFAULT_EXECUTION_POLICY.sell_to_buy_wait_seconds


class OrderExecutor:
    """리밸런싱 플랜에 따라 매도 → 매수 순서로 주문을 실행한다."""

    def __init__(
        self,
        kis_client: KISClient,
        account_type: str,
        is_test: bool = False,
        policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        self.kis_client = kis_client
        self.account_type = account_type
        self.is_test = is_test
        self.policy = policy or DEFAULT_EXECUTION_POLICY

    @log_method_call
    def run_rebalancing(self, plan_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """리밸런싱 플랜에 따라 매도 → 매수 순서로 주문을 실행한다.

        매도를 먼저 실행해 예수금을 확보한 뒤 매수를 진행한다.
        sell/buy 사이에는 체결 후 예수금 반영 대기를 위해 sleep한다.
        """
        sells = plan_df[plan_df['required_transaction'] == 'sell']
        buys = plan_df[plan_df['required_transaction'] == 'buy']

        cash_before_sell = self.kis_client.fetch_domestic_cash_balance()
        logger.info('sell 시작 전 예수금: %s', cash_before_sell)

        result = []
        for i in sells.index:
            result.append(self._execute_order(sells.loc[i].to_dict(), i))

        # sell이 하나라도 있었고 buy도 있을 때만 대기. sell-only 또는 buy-only 시나리오는 skip.
        if sells.shape[0] > 0 and buys.shape[0] > 0:
            wait_seconds = self.policy.sell_to_buy_wait_seconds
            logger.info('sell 완료. %s초 대기 후 buy 시작.', wait_seconds)
            time.sleep(wait_seconds)

        cash_before_buy = self.kis_client.fetch_domestic_cash_balance()
        logger.info('buy 시작 전 예수금: %s', cash_before_buy)

        # 매수 가능 현금은 prvs_rcdl_excc_amt(D+2 예수금)를 기준으로 추적한다.
        # 이전·금일 매도의 미정산 대금이 모두 반영되어 자본 효율이 가장 높다. (docs/kis_cash_guide.md)
        if not buys.empty:
            remaining_cash = self.kis_client.fetch_buy_orderable_cash()
        else:
            remaining_cash = cash_before_buy
        for i in buys.index:
            order_result = self._execute_order(buys.loc[i].to_dict(), i, available_cash=remaining_cash)
            # ARCH-008: 체결 성공한 수량(filled_quantity)만 차감 → 실패·skip 주문은 잔여 보존.
            # calc_price(보수단가) × filled_quantity 사용. calc_price ≥ 실제 체결가이므로
            # 우리 추적 잔여 ≤ KIS 실제 잔여 → 미수 위험 0.
            remaining_cash -= order_result['filled_quantity'] * order_result['calc_price']
            result.append(order_result)
        cash_after_buy = self.kis_client.fetch_domestic_cash_balance()
        logger.info('buy 완료 후 예수금: %s', cash_after_buy)

        # ARCH-004: requested_quantity / filled_quantity 분리.
        # transaction_quantity는 deprecated alias (3개월 후 제거 예정) — 기존 외부 수신부 호환.
        _result_columns = [
            'ticker', 'enable_quantity',
            'requested_quantity', 'filled_quantity', 'transaction_quantity',  # transaction_quantity = filled (deprecated)
            'skipped_reason', 'is_success', 'response_msg', 'transaction_order',
        ]
        return pd.DataFrame(result, columns=_result_columns), cash_after_buy

    def _execute_order(self, plan_row: dict, order_index: int, available_cash: Optional[int] = None) -> dict:
        """단일 종목의 주문을 실행하고 결과를 dict로 반환한다.

        실제 주문 수량은 계획 수량과 주문 가능 수량 중 작은 값을 사용한다.
        is_test=True이거나 주문 가능 수량이 0이면 API를 호출하지 않는다.
        """
        enable_qty, calc_price = self._get_orderable_qty(
            ticker=plan_row['ticker'],
            transaction_type=plan_row['required_transaction'],
            available_cash=available_cash,
        )
        # 계획 수량이 실제 가능 수량을 초과할 수 있으므로 min으로 제한
        transaction_qty = min(plan_row['required_quantity'], enable_qty)

        # requested_quantity: 우리가 KIS에 요청한 수량 (is_test=True여도 실제로 요청한 의도 수량 보존)
        # filled_quantity: KIS 응답 rt_cd='0'으로 성공한 경우만 transaction_qty, 그 외 0
        requested_qty = transaction_qty
        if self.is_test:
            skipped_reason = 'test_mode'
            transaction_qty = 0  # 실제 체결 없음 → 후 비중 계산 시 변화 없도록
            requested_qty = 0    # is_test에서는 KIS에도 요청 안 했으므로 requested도 0
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

        is_success = response['rt_cd'] == '0' if response else None  # KIS API 성공 코드는 '0'
        filled_qty = transaction_qty if is_success is True else 0

        # calc_price는 buys 루프에서 보수 차감용으로만 사용. _result_columns에는 포함되지 않으므로
        # 출력 DataFrame(Slack/BigQuery/Sheets)에는 영향 없다.
        return {
            'ticker': plan_row['ticker'],
            'enable_quantity': enable_qty,
            'requested_quantity': requested_qty,
            'filled_quantity': filled_qty,
            'transaction_quantity': filled_qty,  # deprecated alias = filled (외부 수신부 호환, 3개월 후 제거)
            'calc_price': calc_price,
            'skipped_reason': skipped_reason,
            'is_success': is_success,
            'response_msg': response['msg1'] if response else None,
            'transaction_order': order_index,  # 실행 순서 (Google Sheets 기록용)
        }

    def _get_orderable_qty(self, ticker: str, transaction_type: str, available_cash: Optional[int] = None) -> tuple[int, Optional[int]]:
        """매수 또는 매도 가능 수량을 조회한다.

        매수의 경우:
        - available_cash가 전달되면 해당 값을 기준으로 수량을 계산한다 (연속 매수 시 잔여 현금 추적용).
        - 전달되지 않으면 prvs_rcdl_excc_amt(D+2 예수금) 기준으로 폴백한다.
        가능수량 산정 단가는 inquire-psbl-order의 psbl_qty_calc_unpr을 그대로 사용한다.

        Returns:
            (qty, calc_price): 매수의 경우 calc_price는 inquire-psbl-order의
            psbl_qty_calc_unpr(보수단가). 매도는 calc_price=None.
        """
        if transaction_type == 'buy':
            result = self.kis_client.fetch_domestic_enable_buy(ticker=ticker, ord_dvsn='01')
            calc_price = int(result['psbl_qty_calc_unpr'])
            if available_cash is not None:
                cash_balance = available_cash
            else:
                cash_balance = self.kis_client.fetch_domestic_cash_balance()
            available_amt = cash_balance * self.policy.buy_cash_safety_ratio
            qty = int(available_amt / calc_price) if calc_price > 0 else 0
            logger.info(
                '[%s] cash_balance=%s, available_amt=%s, calc_price=%s → enable_qty=%s',
                ticker, f'{cash_balance:,}', f'{available_amt:,.0f}', f'{calc_price:,}', qty,
            )
            return qty, calc_price
        elif transaction_type == 'sell':
            qty = int(self.kis_client.fetch_domestic_enable_sell(ticker=ticker)['ord_psbl_qty'])
            return qty, None
        raise ValueError(f"transaction_type must be 'buy' or 'sell', got: {transaction_type!r}")
