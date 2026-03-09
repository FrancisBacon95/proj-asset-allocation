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
    '''
    @log_method_call
    def __init__(self, account_type: str) -> None:
        self.auth_config = load_kis_auth_config(account_type)
        self.acc_no = self.auth_config.account_number
        self.acc_no_prefix = self.acc_no.split('-')[0]
        self.acc_no_postfix = self.acc_no.split('-')[1]

        self.mock = False
        self.set_base_url(self.mock)

        self.token_file = PROJECT_ROOT / f'kis_token_{account_type}.dat'
        self.access_token = None
        self._exchange_rate = None

        self.initialize_access_token()

    # ------------------------------------------------------------------ #
    # 인증                                                                  #
    # ------------------------------------------------------------------ #

    def set_base_url(self, mock: bool = True) -> None:
        self.base_url = (
            "https://openapivts.koreainvestment.com:29443" if mock
            else "https://openapi.koreainvestment.com:9443"
        )

    def initialize_access_token(self) -> None:
        if not self.check_access_token():
            self.issue_access_token()
        self.load_access_token()

    def check_access_token(self) -> bool:
        if not self.token_file.exists():
            return False
        with self.token_file.open("rb") as f:
            data = pickle.load(f)
        if (data['app_key'] != self.auth_config.app_key) or (data['app_secret'] != self.auth_config.app_secret):
            return False
        return int(datetime.now().timestamp()) < data['timestamp']

    def issue_access_token(self) -> None:
        """OAuth인증/접근토큰발급"""
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

        timezone = ZoneInfo('Asia/Seoul')
        dt = datetime.strptime(resp_data['access_token_token_expired'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone)
        resp_data['timestamp'] = int(dt.timestamp())
        resp_data['app_key'] = self.auth_config.app_key
        resp_data['app_secret'] = self.auth_config.app_secret

        with self.token_file.open("wb") as f:
            pickle.dump(resp_data, f)

    def load_access_token(self) -> None:
        with self.token_file.open("rb") as f:
            data = pickle.load(f)
        self.access_token = f'Bearer {data["access_token"]}'

    def issue_hashkey(self, data: dict) -> str:
        """해쉬키 발급"""
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

    # ------------------------------------------------------------------ #
    # 잔고 조회                                                             #
    # ------------------------------------------------------------------ #

    @log_method_call
    def fetch_domestic_cash_balance(self) -> int:
        return int(self.fetch_domestic_enable_buy(ticker='005930', ord_dvsn='01')['nrcvb_buy_amt'])

    @log_method_call
    def fetch_domestic_stock_balance(self) -> dict:
        """잔고 조회

        Returns:
            dict: output1(보유 종목 목록), output2(계좌 요약)
        """
        output = {'output1': [], 'output2': []}
        fk100, nk100 = "", ""
        while True:
            data = self._fetch_single_domestic_balance_page(fk100, nk100)
            output['output1'].extend(data['output1'])
            output['output2'].extend(data['output2'])
            if data['tr_cont'] in ('D', 'E'):
                break
            fk100, nk100 = data['ctx_area_fk100'], data['ctx_area_nk100']
        return output

    @log_method_call
    def _fetch_single_domestic_balance_page(self, ctx_area_fk100: str = "", ctx_area_nk100: str = "") -> dict:
        path = "uapi/domestic-stock/v1/trading/inquire-balance"
        url = f"{self.base_url}/{path}"
        headers = {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": "VTTC8434R" if self.mock else "TTTC8434R"
        }
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
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        data['tr_cont'] = res.headers['tr_cont']
        return data

    @log_method_call
    def fetch_oversea_balance(self, exchange_code: str = "NAS") -> dict:
        """해외주식 잔고 조회(구매한 주식에 대한 것만 보여줌, 예수금 X)"""
        output = {'output1': [], 'output2': []}
        fk200, nk200 = "", ""
        while True:
            data = self._fetch_single_oversea_balance_page(fk200, nk200, exchange_code)
            output['output1'].extend(data['output1'])
            output['output2'].extend([data['output2']])
            if data['tr_cont'] in ('D', 'E'):
                break
            fk200, nk200 = data['ctx_area_fk200'], data['ctx_area_nk200']
        return output

    @log_method_call
    def _fetch_single_oversea_balance_page(self, ctx_area_fk200: str = "", ctx_area_nk200: str = "", exchange_code: str = "NAS") -> dict:
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        url = f"{self.base_url}/{path}"
        headers = {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": "VTTS3012R" if self.mock else "TTTS3012R"
        }
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'OVRS_EXCG_CD': exchange_code,
            'TR_CRCY_CD': "USD",
            'CTX_AREA_FK200': ctx_area_fk200,
            'CTX_AREA_NK200': ctx_area_nk200
        }
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        data['tr_cont'] = res.headers['tr_cont']
        return data

    @log_method_call
    def fetch_oversea_cash_balance(self, foreign_currency: bool = True) -> dict:
        """해외주식 체결기준현재잔고
        Args:
            foreign_currency (bool): True: 외화(USD), False: 원화
        """
        path = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        url = f"{self.base_url}/{path}"
        headers = {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": "VTRP6504R" if self.mock else "CTRP6504R"
        }
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            "WCRC_FRCR_DVSN_CD": "02" if foreign_currency else "01",
            "NATN_CD": "840",
            "TR_MKET_CD": "00",
            "INQR_DVSN_CD": "00"
        }
        res = requests.get(url, headers=headers, params=params)
        return res.json()

    @log_method_call
    def fetch_domestic_total_balance(self) -> pd.DataFrame:
        raw_domestic_stock = pd.DataFrame(self.fetch_domestic_stock_balance()['output1'])
        raw_domestic_cash = self.fetch_domestic_cash_balance()
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
        raw_oversea_stock = pd.DataFrame(self.fetch_oversea_balance(exchange_code)['output1'])
        raw_oversea_cash = pd.DataFrame(self.fetch_oversea_cash_balance(False)['output2'])

        oversea_cash = pd.DataFrame(raw_oversea_cash['frcr_evlu_amt2'].rename('current_value'))
        oversea_cash['current_value'] = oversea_cash['current_value'].astype(float)
        oversea_cash['stock_nm'] = 'WON_DEPOSIT'
        oversea_cash['ticker'] = 'CASH'
        oversea_cash['current_price'] = 0
        oversea_cash['current_quantity'] = 0

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
        """국내주식 매수가능조회"""
        path = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        url = f"{self.base_url}/{path}"
        headers = {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": "TTTC8908R"
        }
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'PDNO': ticker,
            'ORD_UNPR': price if ord_dvsn != '01' else '',
            'ORD_DVSN': ord_dvsn,
            'CMA_EVLU_AMT_ICLD_YN': 'N',
            'OVRS_ICLD_YN': 'N'
        }
        res = requests.get(url, headers=headers, params=params)
        return res.json()['output']

    @log_method_call
    def fetch_domestic_enable_sell(self, ticker: str) -> dict:
        """국내주식 매도가능조회"""
        path = "/uapi/domestic-stock/v1/trading/inquire-psbl-sell"
        url = f"{self.base_url}/{path}"
        headers = {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": "TTTC8408R"
        }
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'PDNO': ticker,
        }
        res = requests.get(url, headers=headers, params=params)
        return res.json()['output']

    @log_method_call
    def fetch_domestic_price(self, market_code: str, symbol: str) -> dict:
        """주식현재가시세
        Args:
            market_code (str): 시장 분류코드(J: 주식/ETF/ETN, W: ELW)
            symbol (str): 종목코드(6자리 숫자)
        """
        path = "uapi/domestic-stock/v1/quotations/inquire-price"
        url = f"{self.base_url}/{path}"
        headers = {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": "FHKST01010100"
        }
        params = {
            "fid_cond_mrkt_div_code": market_code,
            "fid_input_iscd": symbol
        }
        resp = requests.get(url, headers=headers, params=params)
        return resp.json()

    @log_method_call
    def fetch_oversea_price(self, symbol: str, exchange_code: str = "NAS") -> dict:
        """해외주식 현재체결가
        Args:
            exchange_code (str): 거래소 코드 (기본값: "NAS" - 나스닥, "NYS" - 뉴욕증시)
        """
        path = "uapi/overseas-price/v1/quotations/price"
        url = f"{self.base_url}/{path}"
        headers = {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": "HHDFS00000300"
        }
        params = {
            "AUTH": "",
            "EXCD": exchange_code,
            "SYMB": symbol
        }
        resp = requests.get(url, headers=headers, params=params)
        return resp.json()

    @property
    def exchange_rate(self) -> float:
        if self._exchange_rate is None:
            import yfinance as yf
            self._exchange_rate = yf.Ticker('KRW=X').history(period='1d')['Close'].iloc[-1]
        return self._exchange_rate

    @log_method_call
    def fetch_price(self, ticker: str, exchange_code: str = "NAS") -> float:
        """국내/해외 종목 현재가 조회 (국내: 원화, 해외: 원화 환산)"""
        if ticker.isdigit() and len(ticker) == 6:
            return float(self.fetch_domestic_price('J', ticker)['output']['stck_prpr'])
        return float(self.fetch_oversea_price(ticker, exchange_code)['output']['last']) * self.exchange_rate

    # ------------------------------------------------------------------ #
    # 휴장일 조회                                                            #
    # ------------------------------------------------------------------ #

    @log_method_call
    def fetch_holiday(self, base_dt: str) -> list:
        """국내 휴장일 조회
        Args:
            base_dt (str): 기준일자 (YYYYMMDD)
        Note:
            모의투자 미지원. 1일 1회 호출 권장.
        """
        path = "uapi/domestic-stock/v1/quotations/chk-holiday"
        url = f"{self.base_url}/{path}"
        headers = {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": "CTCA0903R",
            "custtype": "P"
        }
        params = {
            "BASS_DT": base_dt,
            "CTX_AREA_NK": "",
            "CTX_AREA_FK": ""
        }
        resp = requests.get(url, headers=headers, params=params)
        return resp.json()['output']

    def is_trading_day(self, target_date: date) -> bool:
        """오늘이 개장일이고 결제일인지 확인 (주문 가능 여부)"""
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
        """국내주식 현금 주문
        Args:
            transaction_type (str): 'buy' or 'sell'
            ord_dvsn (str): 주문구분 (00: 지정가, 01: 시장가, ...)
        """
        path = "uapi/domestic-stock/v1/trading/order-cash"
        url = f"{self.base_url}/{path}"
        tr_id = "TTTC0802U" if transaction_type == "buy" else "TTTC0801U"
        unpr = "0" if ord_dvsn == "01" else str(price)
        data = {
            "CANO": self.acc_no_prefix,
            "ACNT_PRDT_CD": self.acc_no_postfix,
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(ord_qty),
            "ORD_UNPR": unpr,
        }
        hashkey = self.issue_hashkey(data)
        headers = {
            "content-type": "application/json",
            "authorization": self.access_token,
            "appKey": self.auth_config.app_key,
            "appSecret": self.auth_config.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "hashkey": hashkey
        }
        resp = requests.post(url, headers=headers, data=json.dumps(data))
        return resp.json()

    @log_method_call
    def buy_domestic_stock(self, ticker: str, ord_qty: int, ord_dvsn: str, price: int = 0) -> dict:
        return self.create_domestic_order(transaction_type='buy', ticker=ticker, price=price, ord_qty=ord_qty, ord_dvsn=ord_dvsn)

    @log_method_call
    def sell_domestic_stock(self, ticker: str, ord_qty: int, ord_dvsn: str, price: int = 0) -> dict:
        return self.create_domestic_order(transaction_type='sell', ticker=ticker, price=price, ord_qty=ord_qty, ord_dvsn=ord_dvsn)
