from typing import Optional

import pandas as pd

from src.kis.client import KISClient
from src.logger import get_logger, log_method_call
from src.planner import PortfolioPlanner
from src.executor import OrderExecutor
from src.policy import DEFAULT_EXECUTION_POLICY, ExecutionPolicy

logger = get_logger(__name__)


class StaticAllocator:
    """목표 비중 기반 국내주식 정적 자산배분 실행기.

    allocation_info(종목별 목표 비중)와 현재 잔고를 비교해
    매수/매도 수량을 계산하고 주문을 실행한다.

    ExecutionPolicy(ARCH-007)는 미주입 시 DEFAULT_EXECUTION_POLICY 사용.
    Planner와 Executor에 동일한 정책 인스턴스를 주입한다.
    """
    @log_method_call
    def __init__(
        self,
        account_type: str,
        allocation_info: pd.DataFrame,
        is_test: bool = False,
        policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        self.account_type = account_type
        self.allocation_info = allocation_info
        self.is_test = is_test
        self.policy = policy or DEFAULT_EXECUTION_POLICY
        logger.info(
            'ExecutionPolicy: buffer_cash=%s, sell_to_buy_wait=%ss, buy_cash_safety_ratio=%s',
            f'{self.policy.buffer_cash:,}',
            self.policy.sell_to_buy_wait_seconds,
            self.policy.buy_cash_safety_ratio,
        )
        kis_client = KISClient(account_type)
        self.planner = PortfolioPlanner(
            kis_client, allocation_info, account_type, policy=self.policy,
        )
        self.executor = OrderExecutor(
            kis_client, account_type, is_test, policy=self.policy,
        )

    def is_trading_day(self, date) -> bool:
        return self.planner.kis_client.is_trading_day(date)

    @log_method_call
    def run(self) -> tuple[pd.DataFrame, int]:
        """리밸런싱 전체 플로우를 실행하고 (플랜+결과 DataFrame, 잔여 예수금) 튜플을 반환한다.

        IRP(개인형 퇴직연금, postfix='29') 계좌는 KIS API로 자동 매매 불가 — 사용자가
        KIS 앱에서 수동으로 거래해야 하므로 거래 실행은 스킵하고 플랜만 반환한다.
        반환 시그니처는 동일 (plan_df, 0).
        """
        total_info = self.planner.get_rebalancing_plan()
        logger.info('total_info:\n%s', total_info.to_string())

        if self.planner.kis_client.is_irp():
            logger.info('IRP 계좌: 자동 매매 불가 — executor 스킵, 플랜만 반환')
            return total_info, 0

        trade_log, remaining_cash = self.executor.run_rebalancing(plan_df=total_info)
        result = total_info.merge(trade_log, on='ticker', how='outer')
        return result, remaining_cash
