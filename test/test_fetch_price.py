"""fetch_price 단위 테스트.

거래 없이 모킹된 응답으로 다음을 검증한다:
- 6자리 숫자 종목코드('379800')와 영문자 섞인 KRX 단축코드('0091C0') 모두
  fetch_domestic_price에 그대로 전달되어 현재가(stck_prpr)를 반환

실행: `uv run python test/test_fetch_price.py`
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))

from src.kis.client import KISClient


def _make_client() -> KISClient:
    return KISClient.__new__(KISClient)


def test_fetch_price_passes_ticker_to_domestic_quote():
    for ticker, prpr in [('379800', '12345'), ('0091C0', '10250'), ('449170', '8800')]:
        c = _make_client()
        c.fetch_domestic_price = MagicMock(return_value={'output': {'stck_prpr': prpr}})
        assert c.fetch_price(ticker) == float(prpr)
        c.fetch_domestic_price.assert_called_once_with('J', ticker)
    print('✅ fetch_price: 숫자/영숫자 KRX 단축코드 모두 국내 시세 조회로 그대로 전달')


if __name__ == '__main__':
    test_fetch_price_passes_ticker_to_domestic_quote()
    print('\n전체 테스트 통과')
