'''
한국투자증권 REST API 클라이언트
'''
import requests
import json
import pickle
from zoneinfo import ZoneInfo
from datetime import date, datetime

import pandas as pd
from src.config.env import PROJECT_ROOT, load_kis_auth_config
from src.logger import get_logger, log_method_call

logger = get_logger(__name__)


class KISClient():
    '''
    한국투자증권 REST API

    모의투자(mock=True)와 실계좌(mock=False) 두 환경을 지원한다.
    tr_id는 환경에 따라 달라지며, 각 메서드에서 self.mock으로 분기한다.
    '''
    @log_method_call
    def __init__(self, account_type: str) -> None:
        self.auth_config = load_kis_auth_config(account_type)
        self.acc_no = self.auth_config.account_number
        # KIS API는 계좌번호를 앞 8자리(prefix)와 뒤 2자리(postfix)로 분리해서 전달한다
        self.acc_no_prefix = self.acc_no.split('-')[0]
        self.acc_no_postfix = self.acc_no.split('-')[1]

        self.mock = False
        self.set_base_url(self.mock)

        self.token_file = PROJECT_ROOT / f'kis_token_{account_type}.dat'
        self.access_token = None
        self._exchange_rate = None  # 환율 캐시 (fetch_price 최초 호출 시 로드)

        self.initialize_access_token()

    # ------------------------------------------------------------------ #
    # 인증                                                                  #
    # ------------------------------------------------------------------ #

    def set_base_url(self, mock: bool = True) -> None:
        """모의투자 여부에 따라 base_url을 설정한다."""
        self.base_url = (
            "https://openapivts.koreainvestment.com:29443" if mock
            else "https://openapi.koreainvestment.com:9443"
        )

    def initialize_access_token(self) -> None:
        """토큰 파일이 유효하면 로드하고, 만료됐거나 없으면 새로 발급한다."""
        if not self.check_access_token():
            self.issue_access_token()
        self.load_access_token()

    def check_access_token(self) -> bool:
        """저장된 토큰이 유효한지 확인한다.

        토큰 파일이 없거나, 앱키가 다르거나, 만료 시각이 지났으면 False를 반환한다.
        """
        if not self.token_file.exists():
            return False
        with self.token_file.open("rb") as f:
            data = pickle.load(f)
        # 발급 당시와 앱키/시크릿이 다르면 재발급 필요
        if (data['app_key'] != self.auth_config.app_key) or (data['app_secret'] != self.auth_config.app_secret):
            return False
        return int(datetime.now().timestamp()) < data['timestamp']

    def issue_access_token(self) -> None:
        """OAuth 액세스 토큰을 발급받아 파일에 저장한다."""
        path = "oauth2/tokenP"
        url = f"{self.base_url}/{path}"
        headers = {"content-type": "application/json"}
        data = {
            "grant_type": "client_credentials",
            "appkey": self.auth_config.app_key,
            "appsecret": self.auth_config.app_secret,
        }
        resp = requests.post(url, headers=headers, json=data)
        resp_data = resp.json()
        self.access_token = f'Bearer {resp_data["access_token"]}'

        # 만료 시각을 타임스탬프로 변환해 함께 저장 (유효성 검사에 사용)
        timezone = ZoneInfo('Asia/Seoul')
        dt = datetime.strptime(resp_data['access_token_token_expired'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone)
        resp_data['timestamp'] = int(dt.timestamp())
        resp_data['app_key'] = self.auth_config.app_key
        resp_data['app_secret'] = self.auth_config.app_secret

        with self.token_file.open("wb") as f:
            pickle.dump(resp_data, f)

    def load_access_token(self) -> None:
        """파일에서 액세스 토큰을 읽어 self.access_token에 설정한다."""
        with self.token_file.open("rb") as f:
            data = pickle.load(f)
        self.access_token = f'Bearer {data["access_token"]}'

    def issue_hashkey(self, data: dict) -> str:
        """POST 요청 위변조 방지를 위한 해시키를 발급받는다.

        주문 API 호출 시 request body를 해시키로 서명해야 한다.
        이 엔드포인트는 authorization 헤더 없이 appKey/appSecret만으로 호출한다.
        """
        path = "uapi/hashkey"
        url = f"{self.base_url}/{path}"
        headers = {
            "content-type": "application/json",
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "User-Agent": "Mozilla/5.0"
        }
        resp = requests.post(url, headers=headers, data=json.dumps(data))
        return resp.json()["HASH"]

    def _headers(self, tr_id: str, **extra) -> dict:
        """공통 요청 헤더를 생성한다. 추가 헤더는 **extra로 전달한다."""
        return {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": tr_id,
            **extra,
        }

    def _get(self, path: str, tr_id: str, params: dict, **extra) -> requests.Response:
        """공통 GET 요청 헬퍼."""
        return requests.get(
            f"{self.base_url}/{path}",
            headers=self._headers(tr_id, **extra),
            params=params,
        )

    def _post(self, path: str, tr_id: str, data: dict, **extra) -> requests.Response:
        """공통 POST 요청 헬퍼."""
        return requests.post(
            f"{self.base_url}/{path}",
            headers=self._headers(tr_id, **extra),
            data=json.dumps(data),
        )

    # ------------------------------------------------------------------ #
    # 잔고 조회                                                             #
    # ------------------------------------------------------------------ #

    @log_method_call
    def fetch_domestic_cash_balance(self) -> int:
        """국내 계좌 예수금(원화 현금)을 조회한다."""
        data = self._domestic_balance_page()
        return int(data['output2'][0]['dnca_tot_amt'])

    @log_method_call
    def fetch_domestic_stock_balance(self) -> dict:
        """국내 보유 주식 잔고를 전체 페이지 조회해 반환한다.

        Returns:
            dict: output1(보유 종목 목록), output2(계좌 요약)
        """
        output = {'output1': [], 'output2': []}
        fk100, nk100 = "", ""
        while True:
            data = self._domestic_balance_page(fk100, nk100)
            output['output1'].extend(data['output1'])
            output['output2'].extend(data['output2'])
            # tr_cont가 'D' 또는 'E'이면 마지막 페이지
            if data['tr_cont'] in ('D', 'E'):
                break
            fk100, nk100 = data['ctx_area_fk100'], data['ctx_area_nk100']
        return output

    @log_method_call
    def _domestic_balance_page(self, ctx_area_fk100: str = "", ctx_area_nk100: str = "") -> dict:
        """국내 잔고 단일 페이지를 조회한다. 페이지네이션 연속 키를 포함해 반환한다."""
        tr_id = "VTTC8434R" if self.mock else "TTTC8434R"
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'AFHR_FLPR_YN': 'N',
            'OFL_YN': 'N',
            'INQR_DVSN': '01',
            'UNPR_DVSN': '01',
            'FUND_STTL_ICLD_YN': 'N',
            'FNCG_AMT_AUTO_RDPT_YN': 'N',
            'PRCS_DVSN': '01',
            'CTX_AREA_FK100': ctx_area_fk100,
            'CTX_AREA_NK100': ctx_area_nk100
        }
        res = self._get("uapi/domestic-stock/v1/trading/inquire-balance", tr_id, params)
        data = res.json()
        # 다음 페이지 존재 여부는 응답 body가 아닌 응답 헤더의 tr_cont로 판단한다
        data['tr_cont'] = res.headers['tr_cont']
        return data

    @log_method_call
    def fetch_oversea_balance(self, exchange_code: str = "NAS") -> dict:
        """해외 보유 주식 잔고를 전체 페이지 조회해 반환한다. (현금 제외)"""
        output = {'output1': [], 'output2': []}
        fk200, nk200 = "", ""
        while True:
            data = self._oversea_balance_page(fk200, nk200, exchange_code)
            output['output1'].extend(data['output1'])
            output['output2'].extend([data['output2']])
            if data['tr_cont'] in ('D', 'E'):
                break
            fk200, nk200 = data['ctx_area_fk200'], data['ctx_area_nk200']
        return output

    @log_method_call
    def _oversea_balance_page(self, ctx_area_fk200: str = "", ctx_area_nk200: str = "", exchange_code: str = "NAS") -> dict:
        """해외 잔고 단일 페이지를 조회한다. 페이지네이션 연속 키를 포함해 반환한다."""
        tr_id = "VTTS3012R" if self.mock else "TTTS3012R"
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'OVRS_EXCG_CD': exchange_code,
            'TR_CRCY_CD': "USD",
            'CTX_AREA_FK200': ctx_area_fk200,
            'CTX_AREA_NK200': ctx_area_nk200
        }
        res = self._get("uapi/overseas-stock/v1/trading/inquire-balance", tr_id, params)
        data = res.json()
        data['tr_cont'] = res.headers['tr_cont']
        return data

    @log_method_call
    def fetch_oversea_cash_balance(self, foreign_currency: bool = True) -> dict:
        """해외 체결기준 현재잔고를 조회한다.

        Args:
            foreign_currency (bool): True이면 외화(USD), False이면 원화 환산
        """
        tr_id = "VTRP6504R" if self.mock else "CTRP6504R"
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            "WCRC_FRCR_DVSN_CD": "02" if foreign_currency else "01",
            "NATN_CD": "840",   # 840 = 미국
            "TR_MKET_CD": "00",
            "INQR_DVSN_CD": "00"
        }
        return self._get("uapi/overseas-stock/v1/trading/inquire-present-balance", tr_id, params).json()

    @log_method_call
    def fetch_domestic_total_balance(self) -> pd.DataFrame:
        """국내 전체 잔고(주식 + 현금)를 단일 DataFrame으로 반환한다.

        현금은 ticker='CASH', stock_nm='WON_DEPOSIT'으로 행을 추가해 통합한다.
        """
        raw_domestic_stock = pd.DataFrame(self.fetch_domestic_stock_balance()['output1'])
        raw_domestic_cash = self.fetch_domestic_cash_balance()

        # 현금을 주식 잔고와 같은 스키마의 단일 행으로 만든다
        domestic_cash = pd.DataFrame([{
            'ticker': 'CASH',
            'stock_nm': 'WON_DEPOSIT',
            'current_quantity': 0,
            'current_price': 0,
            'current_value': raw_domestic_cash,
        }])
        domestic_stock = raw_domestic_stock[['pdno', 'prdt_name', 'hldg_qty', 'prpr', 'evlu_amt']].rename(
            columns={
                'pdno': 'ticker',
                'prdt_name': 'stock_nm',
                'hldg_qty': 'current_quantity',
                'prpr': 'current_price',
                'evlu_amt': 'current_value',
            }
        )
        domestic = pd.concat([domestic_stock, domestic_cash[domestic_stock.columns]]).reset_index(drop=True)
        domestic['currency_type'] = 'domestic'
        domestic[['current_price', 'current_value', 'current_quantity']] = domestic[['current_price', 'current_value', 'current_quantity']].astype(float)
        return domestic

    @log_method_call
    def fetch_oversea_total_balance(self, exchange_code: str = "NAS") -> pd.DataFrame:
        """해외 전체 잔고(주식 + 현금)를 단일 DataFrame으로 반환한다.

        주식 가격은 API에서 받은 환율로 원화 환산해 반환한다.
        현금은 ticker='CASH', stock_nm='WON_DEPOSIT'으로 행을 추가해 통합한다.
        """
        raw_oversea_stock = pd.DataFrame(self.fetch_oversea_balance(exchange_code)['output1'])
        raw_oversea_cash = pd.DataFrame(self.fetch_oversea_cash_balance(False)['output2'])

        oversea_cash = pd.DataFrame(raw_oversea_cash['frcr_evlu_amt2'].rename('current_value'))
        oversea_cash['current_value'] = oversea_cash['current_value'].astype(float)
        oversea_cash['stock_nm'] = 'WON_DEPOSIT'
        oversea_cash['ticker'] = 'CASH'
        oversea_cash['current_price'] = 0
        oversea_cash['current_quantity'] = 0

        # 환율은 현금 잔고 응답에 포함된 최초 고시 환율을 사용한다
        exchange_rate = raw_oversea_cash['frst_bltn_exrt'].astype(float)[0]

        raw_oversea_stock['ovrs_cblc_qty'] = raw_oversea_stock['ovrs_cblc_qty'].astype(int)
        raw_oversea_stock['now_pric2'] = raw_oversea_stock['now_pric2'].astype(float)

        oversea_stock = raw_oversea_stock[['ovrs_pdno', 'ovrs_item_name', 'ovrs_cblc_qty', 'now_pric2']].rename(
            columns={
                'ovrs_pdno': 'ticker',
                'ovrs_item_name': 'stock_nm',
                'ovrs_cblc_qty': 'current_quantity',
                'now_pric2': 'current_price',
            }
        )
        # 외화 가격을 원화로 환산
        oversea_stock['current_price'] *= exchange_rate
        oversea_stock['current_value'] = oversea_stock['current_price'] * oversea_stock['current_quantity']

        oversea = pd.concat([oversea_stock, oversea_cash[oversea_stock.columns]]).reset_index(drop=True)
        oversea['currency_type'] = 'oversea'
        return oversea

    # ------------------------------------------------------------------ #
    # 시세 조회                                                             #
    # ------------------------------------------------------------------ #

    @log_method_call
    def fetch_domestic_enable_buy(self, ticker: str, ord_dvsn: str = '01', price: int = -1) -> dict:
        """국내주식 매수 가능 금액/수량을 조회한다.

        Args:
            ord_dvsn (str): 주문구분 (01: 시장가, 00: 지정가)
            price (int): 지정가 주문 시 주문단가. 시장가(ord_dvsn='01')일 때는 무시된다.
        """
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'PDNO': ticker,
            'ORD_UNPR': price if ord_dvsn != '01' else '',  # 시장가이면 빈 문자열
            'ORD_DVSN': ord_dvsn,
            'CMA_EVLU_AMT_ICLD_YN': 'Y',
            'OVRS_ICLD_YN': 'N'
        }
        return self._get("uapi/domestic-stock/v1/trading/inquire-psbl-order", "TTTC8908R", params).json()['output']

    @log_method_call
    def fetch_domestic_enable_sell(self, ticker: str) -> dict:
        """국내주식 매도 가능 수량을 조회한다."""
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'PDNO': ticker,
        }
        return self._get("uapi/domestic-stock/v1/trading/inquire-psbl-sell", "TTTC8408R", params).json()['output']

    @log_method_call
    def fetch_domestic_price(self, market_code: str, symbol: str) -> dict:
        """국내주식 현재가 시세를 조회한다.

        Args:
            market_code (str): 시장 분류코드 (J: 주식/ETF/ETN, W: ELW)
            symbol (str): 종목코드 (6자리 숫자)
        """
        params = {
            "fid_cond_mrkt_div_code": market_code,
            "fid_input_iscd": symbol
        }
        return self._get("uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", params).json()

    @log_method_call
    def fetch_oversea_price(self, symbol: str, exchange_code: str = "NAS") -> dict:
        """해외주식 현재 체결가를 조회한다.

        Args:
            exchange_code (str): 거래소 코드 (기본값: "NAS" - 나스닥, "NYS" - 뉴욕증시)
        """
        params = {
            "AUTH": "",
            "EXCD": exchange_code,
            "SYMB": symbol
        }
        return self._get("uapi/overseas-price/v1/quotations/price", "HHDFS00000300", params).json()

    @property
    def exchange_rate(self) -> float:
        """USD/KRW 환율을 반환한다. 최초 접근 시 yfinance로 조회하고 이후 캐시를 사용한다."""
        if self._exchange_rate is None:
            import yfinance as yf
            self._exchange_rate = yf.Ticker('KRW=X').history(period='1d')['Close'].iloc[-1]
        return self._exchange_rate

    @log_method_call
    def fetch_price(self, ticker: str, exchange_code: str = "NAS") -> float:
        """국내/해외 종목의 현재가를 원화로 반환한다.

        종목코드가 6자리 숫자이면 국내주식으로 판단하고, 그 외는 해외주식으로 조회한다.
        해외주식은 exchange_rate를 곱해 원화로 환산한다.
        """
        if ticker.isdigit() and len(ticker) == 6:
            return float(self.fetch_domestic_price('J', ticker)['output']['stck_prpr'])
        return float(self.fetch_oversea_price(ticker, exchange_code)['output']['last']) * self.exchange_rate

    # ------------------------------------------------------------------ #
    # 휴장일 조회                                                            #
    # ------------------------------------------------------------------ #

    @log_method_call
    def fetch_holiday(self, base_dt: str) -> list:
        """국내 휴장일 목록을 조회한다.

        Args:
            base_dt (str): 기준일자 (YYYYMMDD). 해당 날짜 이후의 휴장일 목록을 반환한다.
        Note:
            모의투자 미지원. 1일 1회 호출 권장.
        """
        params = {
            "BASS_DT": base_dt,
            "CTX_AREA_NK": "",
            "CTX_AREA_FK": ""
        }
        return self._get("uapi/domestic-stock/v1/quotations/chk-holiday", "CTCA0903R", params, custtype="P").json()['output']

    def is_trading_day(self, target_date: date) -> bool:
        """해당 날짜가 개장일이면서 결제일인지 확인한다 (주문 실행 가능 여부 판단용)."""
        base_dt = target_date.strftime('%Y%m%d')
        holidays = self.fetch_holiday(base_dt)
        today = next((h for h in holidays if h['bass_dt'] == base_dt), None)
        if today is None:
            return False
        return today['opnd_yn'] == 'Y' and today['sttl_day_yn'] == 'Y'

    # ------------------------------------------------------------------ #
    # 주문                                                                  #
    # ------------------------------------------------------------------ #

    @log_method_call
    def create_domestic_order(self, transaction_type: str, ticker: str, ord_qty: int, ord_dvsn: str, price: int = -1) -> dict:
        """국내주식 현금 주문(매수/매도)을 실행한다.

        Args:
            transaction_type (str): 'buy' (매수) 또는 'sell' (매도)
            ord_dvsn (str): 주문구분 (00: 지정가, 01: 시장가, ...)
            price (int): 지정가 주문 시 주문단가. 시장가(ord_dvsn='01')이면 무시된다.
        """
        tr_id = "TTTC0802U" if transaction_type == "buy" else "TTTC0801U"
        unpr = "0" if ord_dvsn == "01" else str(price)  # 시장가이면 단가를 "0"으로 전달
        data = {
            "CANO": self.acc_no_prefix,
            "ACNT_PRDT_CD": self.acc_no_postfix,
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(ord_qty),
            "ORD_UNPR": unpr,
        }
        # 주문 API는 request body 위변조 방지를 위해 해시키 서명이 필수
        hashkey = self.issue_hashkey(data)
        return self._post("uapi/domestic-stock/v1/trading/order-cash", tr_id, data, custtype="P", hashkey=hashkey).json()