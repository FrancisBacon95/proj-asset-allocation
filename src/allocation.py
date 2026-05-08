import pandas as pd

from src.kis.client import KISClient
from src.logger import get_logger, log_method_call
from src.planner import PortfolioPlanner
from src.executor import OrderExecutor

logger = get_logger(__name__)


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

    def is_trading_day(self, date) -> bool:
        return self.planner.kis_client.is_trading_day(date)

    @log_method_call
    def run(self) -> tuple[pd.DataFrame, int]:
        """리밸런싱 전체 플로우를 실행하고 (플랜+결과 DataFrame, 잔여 예수금) 튜플을 반환한다."""
        total_info = self.planner.get_rebalancing_plan()
        logger.info('total_info:\n%s', total_info.to_string())
        trade_log, remaining_cash = self.executor.run_rebalancing(plan_df=total_info)
        result = total_info.merge(trade_log, on='ticker', how='outer')
        return result, remaining_cash
