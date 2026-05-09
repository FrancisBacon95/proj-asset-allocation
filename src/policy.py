"""실행 정책 (ARCH-007).

리밸런싱 실행 시 계좌별·환경별로 튜닝 가능한 상수를 한 곳에 모은다.
기본값은 종전 모듈 상수와 동일하게 두어 동작 변경 없이 점진 도입.

PortfolioPlanner와 OrderExecutor는 본 정책 객체를 주입받아 사용한다.
StaticAllocator가 합성 진입점에서 정책 인스턴스를 만들어 양쪽에 넘긴다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionPolicy:
    """리밸런싱 실행 정책.

    Attributes:
        buffer_cash:
            리밸런싱 후 보존할 최소 예수금(원).
            target_value 산정 시 총평가금액에서 차감해 매수 한도를 줄인다.
            (기존: src.planner.BUFFER_CASH = 10_000)

        sell_to_buy_wait_seconds:
            sell 체결 → 예수금 반영 → buy 주문 사이의 대기 시간(초).
            KIS 결제 반영 지연을 흡수해 매수 단계 미수 위험을 낮춘다.
            (기존: src.executor.SELL_TO_BUY_WAIT_SECONDS = 3)

        buy_cash_safety_ratio:
            매수 시 보수단가(psbl_qty_calc_unpr) 기준 가용현금에 곱하는 안전 마진.
            1.0 미만이어야 보수적. 0.99면 KIS 보수단가 기반 잔여추적이 1% 마진을 둔다.
            (기존: src.executor._get_orderable_qty 의 0.99 매직 넘버)
    """

    buffer_cash: int = 10_000
    sell_to_buy_wait_seconds: int = 3
    buy_cash_safety_ratio: float = 0.99

    def __post_init__(self) -> None:
        if self.buffer_cash < 0:
            raise ValueError(f"buffer_cash >= 0이어야 합니다: {self.buffer_cash}")
        if self.sell_to_buy_wait_seconds < 0:
            raise ValueError(
                f"sell_to_buy_wait_seconds >= 0이어야 합니다: {self.sell_to_buy_wait_seconds}"
            )
        if not (0.0 < self.buy_cash_safety_ratio <= 1.0):
            raise ValueError(
                f"buy_cash_safety_ratio는 (0, 1] 범위여야 합니다: {self.buy_cash_safety_ratio}"
            )


# 모듈 전역 기본 인스턴스. 호출부에서 명시 주입이 없을 때 사용.
DEFAULT_EXECUTION_POLICY = ExecutionPolicy()
