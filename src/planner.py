from typing import Optional

import pandas as pd

from src.kis.client import KISClient
from src.logger import get_logger, log_method_call
from src.policy import DEFAULT_EXECUTION_POLICY, ExecutionPolicy

logger = get_logger(__name__)

# Deprecated: ExecutionPolicy.buffer_cash로 이동 (ARCH-007).
# 외부에서 import하는 호출부 호환을 위해 모듈 상수도 유지하되 본 모듈 내부에서는 사용하지 않는다.
BUFFER_CASH = DEFAULT_EXECUTION_POLICY.buffer_cash


class PortfolioPlanner:
    """목표 비중과 현재 잔고를 바탕으로 리밸런싱 플랜을 계산한다."""

    def __init__(
        self,
        kis_client: KISClient,
        allocation_info: pd.DataFrame,
        account_type: str,
        policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        self.kis_client = kis_client
        self.allocation_info = allocation_info
        self.account_type = account_type
        self.policy = policy or DEFAULT_EXECUTION_POLICY
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
        if df['weight'].isnull().any():
            raise ValueError("weight에 빈 값(NaN)이 있습니다.")
        if (df['weight'] < 0).any():
            raise ValueError("weight에 음수 값이 있습니다.")
        weight_sum = df['weight'].sum()
        if weight_sum > 1.0 + 1e-9:
            raise ValueError(f"weight 합계가 1.0을 초과합니다: {weight_sum:.4f}")

    @log_method_call
    def _create_total_info(self, allocation: pd.DataFrame, balance: pd.DataFrame) -> pd.DataFrame:
        """목표 비중과 현재 잔고를 합산해 종목별 거래 계획을 계산한다.

        총 잔고 금액을 기준으로 목표 금액(target_value)을 산출하고,
        현재 금액과의 차이로 필요 거래 수량과 방향(buy/sell)을 결정한다.
        """
        buffer_cash = self.policy.buffer_cash
        total_balance_value = balance['current_value'].sum()
        if total_balance_value <= buffer_cash:
            raise ValueError(
                f"총 평가금액({total_balance_value:,.0f}원)이 버퍼({buffer_cash:,}원) 이하입니다. 리밸런싱 중단."
            )
        logger.info('총 평가금액 (예수금 포함): %s원', f'{total_balance_value:,.0f}')

        # 목표 비중 테이블에 현재 잔고를 left join. 미보유 종목은 current_quantity/value를 0으로 채운다.
        result = pd.merge(left=allocation, right=balance, on=['ticker'], how='left')
        result[['current_quantity', 'current_value']] = result[['current_quantity', 'current_value']].fillna(0)

        invalid_prices = result[~(result['current_price'] > 0)]
        if not invalid_prices.empty:
            raise ValueError(f"유효하지 않은 가격(0 또는 NaN)이 있는 종목: {invalid_prices['ticker'].tolist()}")

        # 리밸런싱 후 최소 예수금 버퍼 확보를 위해 정책의 buffer_cash를 차감한 금액을 기준으로 목표 금액을 계산한다.
        result['target_value'] = result['weight'] * int(total_balance_value - buffer_cash)

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
