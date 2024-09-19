'''
한국투자증권 python wrapper
'''
from src.auth.kis_auth import KISAuth
from src.core.stock_config import *
import yfinance as yf
import json

import pandas as pd
import requests

class KISAgent(KISAuth):
    '''
    한국투자증권 REST API
    '''
    def __init__(self, account_type) -> None:
        """생성자
        Args:
            app_key (str): 발급받은 API key
            app_secret (str): 발급받은 API secret
            acc_no (str): 계좌번호 체계의 앞 8자리-뒤 2자리
            exchange (str): "서울", "나스닥", "뉴욕", "아멕스", "홍콩", "상해", "심천",
- "도쿄", "하노이", "호치민"
            mock (bool): True (mock trading), False (real trading)
        """
        super().__init__(account_type)
        self.exchange_rate = yf.Ticker('KRW=X').history(period='1d')['Close'].iloc[-1]

    def fetch_domestic_balance(self) -> dict:
        """잔고 조회

        Args:

        Returns:
            dict: response data
        - output1
            - pdno: 상품번호
            - prdt_name: 상품명
            - trad_dvsn_name: 매매구분명
            - bfdy_buy_qty: 전일매수수량
            - bfdy_sll_qty: 전일매도수량
            - thdt_buyqty: 금일매수수량
            - thdt_sll_qty: 금일매도수량
            - hldg_qty: 보유수량
            - ord_psbl_qty: 주문가능수량
            - pchs_avg_pric: 매입평균가격
            - pchs_amt: 매입금액
            - prpr: 현재가
            - evlu_amt: 평가금액
            - evlu_pfls_amt: 평가손익금액
            - evlu_pfls_rt: 평가손익율
            - evlu_erng_rt: 평가수익율
            - loan_dt: 대출일자
            - loan_amt: 대출금액
            - stln_slng_chgs: 대주매각대금
            - expd_dt: 만기일자
            - fltt_rt: 등락율
            - bfdy_cprs_icdc: 전일대비증감
            - item_mgna_rt_name: 종목증거금율명
            - grta_rt_name: 보증금율명
            - sbst_pric: 대용가격
            - stck_loan_unpr: 주식대출단가
        - output2
            - dnca_tot_amt: 예수금총금액
            - nxdy_excc_amt: 익일정산금액
            - prvs_rcdl_excc_amt: 가수도정산금액
            - cma_evlu_amt: CMA평가금액
            - bfdy_buy_amt: 전일매수금액
            - thdt_buy_amt: 금일매수금액
            - nxdy_auto_rdpt_amt: 익일자동상환금액
            - bfdy_sll_amt: 전일매도금액
            - thdt_sll_amt: 금일매도금액
            - d2_auto_rdpt_amt: D+2자동상환금액	
            - bfdy_tlex_amt: 전일제비용금액
            - thdt_tlex_amt: 금일제비용금액
            - tot_loan_amt: 총대출금액
            - scts_evlu_amt: 유가평가금액
            - tot_evlu_amt: 총평가금액
            - nass_amt: 순자산금액
            - fncg_gld_auto_rdpt_yn: 융자금자동상환여부
            - pchs_amt_smtl_amt: 매입금액합계금액
            - evlu_amt_smtl_amt: 평가금액합계금액
            - evlu_pfls_smtl_amt: 평가손익합계금액
            - tot_stln_slng_chgs: 총대주매각대금
            - bfdy_tot_asst_evlu_amt: 전일총자산평가금액
            - asst_icdc_amt: 자산증감액
            - asst_icdc_erng_rt: 자산증감수익율
        """
        output = {'output1': [], 'output2': []}
        fk100, nk100 = "", ""

        while True:
            # 페이지 데이터 조회
            data = self._fetch_single_domestic_balance_page(fk100, nk100)
            output['output1'].extend(data['output1'])
            output['output2'].extend(data['output2'])

            # 다음 페이지 확인
            if data['tr_cont'] in ('D', 'E'):
                break
            
            fk100, nk100 = data['ctx_area_fk100'], data['ctx_area_nk100']
        return output
    
    def fetch_oversea_balance(self) -> dict:
        # 해외주식 잔고
        """주식 잔고 조회(구매한 주식에 대한 것만 보여줌, 예수금 X)
        Args:

        Returns:
            dict: response data
        - ouput1
            - cano: 종합계좌번호
            - acnt_prdt_cd: 계좌상품코드
            - prdt_type_cd: 상품유형코드
            - ovrs_pdno: 해외상품번호
            - ovrs_item_name: 해외종목명
            - frcr_evlu_pfls_amt: 외화평가손익금액
            - evlu_pfls_rt: 평가손익율
            - pchs_avg_pric: 매입평균가격(USD)
            - ovrs_cblc_qty: 해외잔고수량 = 보유수량
            - ord_psbl_qty: 주문가능수량
            - frcr_pchs_amt1: 외화매입금액1 = 매입금액(USD) = 보유수량 * 매입평균가격(USD) = ovrs_cblc_qty * pchs_avg_pric
            - ovrs_stck_evlu_amt: 해외주식평가금액 = 평가금액(USD) = 보유수량 * 현재가격(USD) = ovrs_cblc_qty * now_pric2 
            - now_pric2: 현재가격2
            - tr_crcy_cd: 거래통화코드
            - ovrs_excg_cd: 해외거래소코드
            - loan_type_cd: 대출유형코드
            - loan_dt: 대출일자
            - expd_dt: 만기일자
        - output2(필요한 정보 거의 없다고 보는 게 맞음)
            - frcr_pchs_amt1: 외화매입금액1
            - ovrs_rlzt_pfls_amt: 해외실현손익금액
            - ovrs_tot_pfls: 해외총손익
            - rlzt_erng_rt: 실현수익율
            - tot_evlu_pfls_amt: 총평가손익금액
            - tot_pftrt: 총수익률
            - frcr_buy_amt_smtl1: 외화매수금액합계1
            - ovrs_rlzt_pfls_amt2: 해외실현손익금액2
            - frcr_buy_amt_smtl2: 외화매수금액합계2
        """
        output = {'output1': [], 'output2': []}
        fk200, nk200 = "", ""

        while True:
            # 페이지 데이터 조회
            data = self._fetch_single_oversea_balance_page(fk200, nk200)
            output['output1'].extend(data['output1'])
            output['output2'].extend([data['output2']])

            # 다음 페이지 확인
            if data['tr_cont'] in ('D', 'E'):
                break
            
            fk200, nk200 = data['ctx_area_fk200'], data['ctx_area_nk200']
        return output

    def _fetch_single_domestic_balance_page(self, ctx_area_fk100: str = "", ctx_area_nk100: str = "") -> dict:
        """국내주식주문/주식잔고조회
        Args:
            ctx_area_fk100 (str): 연속조회검색조건100
            ctx_areak_nk100 (str): 연속조회키100
        Returns:
            dict: _description_
        """
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

    def _fetch_single_oversea_balance_page(self, ctx_area_fk200: str = "", ctx_area_nk200: str = "") -> dict:
        """해외주식주문/해외주식 잔고
        Args:
            ctx_area_fk200 (str): 연속조회검색조건200
            ctx_area_nk200 (str): 연속조회키200
        Returns:
            dict: _description_
        """
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        url = f"{self.base_url}/{path}"

        # request header
        headers = {
           "content-type": "application/json",
           "authorization": self.access_token,
           "appKey": self.auth_config.app_key,
           "appSecret": self.auth_config.app_secret,
           "tr_id": "VTTS3012R" if self.mock else "TTTS3012R"
        }

        # query parameter
        exchange_cd = "NAS"
        currency_cd = "USD"

        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'OVRS_EXCG_CD': exchange_cd,
            'TR_CRCY_CD': currency_cd,
            'CTX_AREA_FK200': ctx_area_fk200,
            'CTX_AREA_NK200': ctx_area_nk200
        }

        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        data['tr_cont'] = res.headers['tr_cont']
        return data

    def fetch_oversea_present_balance(self, foreign_currency: bool=True) -> dict:
        """해외주식주문/해외주식 체결기준현재잔고
        Args:
            foreign_currency (bool): True: 외화, False: 원화
        Returns:
            dict:
        - output1(foreign_currency에 따라 USD/WON이 정해짐)
            - frcr_pchs_amt: 매입금액(USD/WON) = 매입단가(USD/WON) * 보유수량 = avg_unpr3 * ccld_qty_smtl1
            - frcr_evlu_amt2: 평가금액(USD/WON) = 현재가(USD/WON * 보유수량 = ovrs_now_pric1 * ccld_qty_smtl1
            - ccld_qty_smtl1: 보유수량
            - ovrs_now_pric1: 현재가(USD/WON)
            - avg_unpr3: 매입단가(USD/WON)
        - output2(foreign_currency의 영향을 받지 않음)
            - frcr_dncl_amt_2: 외화잔고(USD)
            - frcr_drwg_psbl_amt_1
            - nxdy_frcr_drwg_psbl_amt
            - frst_bltn_exrt: 원달러환율
            - frcr_evlu_amt2: 평가금액(WON) = 외화잔고(USD) * 원달러환률 = frcr_dncl_amt_2 * frst_bltn_exrt 
        """
        path = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        url = f"{self.base_url}/{path}"

        # request header
        headers = {
           "content-type": "application/json",
           "authorization": self.access_token,
           "appKey": self.auth_config.app_key,
           "appSecret": self.auth_config.app_secret,
           "tr_id": "VTRP6504R" if self.mock else "CTRP6504R"
        }

        # query parameter

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
    
    def fetch_domestic_price(self, market_code: str, symbol: str) -> dict:
        """주식현재가시세
        Args:
            market_code (str): 시장 분류코드(J: 주식/ETF/ETN, W: ELW)
            symbol (str): 종목코드(6자리 숫자, ETN은 Q로 시작)
        Returns:
            dict: API 개발 가이드 참조
        - output
            - iscd_stat_cls_code: 종목 상태 구분 코드
            - marg_rate: 증거금 비율
            - rprs_mrkt_kor_name: 대표 시장 한글 명
            - new_hgpr_lwpr_cls_code: 신 고가 저가 구분 코드
            - bstp_kor_isnm: 업종 한글 종목명
            - temp_stop_yn: 임시 정지 여부
            - oprc_rang_cont_yn: 시가 범위 연장 여부
            - clpr_rang_cont_yn: 종가 범위 연장 여부
            - crdt_able_yn: 신용 가능 여부
            - grmn_rate_cls_code: 보증금 비율 구분 코드
            - elw_pblc_yn: ELW 발행 여부
            - stck_prpr: 주식 현재가
            - prdy_vrss: 전일 대비
            - prdy_vrss_sign: 전일 대비 부호
            - prdy_ctrt: 전일 대비율
            - acml_tr_pbmn: 누적 거래 대금
            - acml_vol: 누적 거래량
            - prdy_vrss_vol_rate: 전일 대비 거래량 비율
            - stck_oprc: 주식 시가
            - stck_hgpr: 주식 최고가
            - stck_lwpr: 주식 최저가
            - stck_mxpr: 주식 상한가
            - stck_llam: 주식 하한가
            - stck_sdpr: 주식 기준가
            - wghn_avrg_stck_prc: 가중 평균 주식 가격
            - hts_frgn_ehrt: HTS 외국인 소진율
            - frgn_ntby_qty: 외국인 순매수 수량
            - pgtr_ntby_qty: 프로그램매매 순매수 수량
            - pvt_scnd_dmrs_prc: 피벗 2차 디저항 가격
            - pvt_frst_dmrs_prc: 피벗 1차 디저항 가격
            - pvt_pont_val: 피벗 포인트 값
            - pvt_frst_dmsp_prc: 피벗 1차 디지지 가격
            - pvt_scnd_dmsp_prc: 피벗 2차 디지지 가격
            - dmrs_val: 디저항 값
            - dmsp_val: 디지지 값
            - cpfn: 자본금
            - rstc_wdth_prc: 제한 폭 가격
            - stck_fcam: 주식 액면가
            - stck_sspr: 주식 대용가
            - aspr_unit: 호가단위
            - hts_deal_qty_unit_val: HTS 매매 수량 단위 값
            - lstn_stcn: 상장 주수
            - hts_avls: HTS 시가총액
            - per: PER
            - pbr: PBR
            - stac_month: 결산 월
            - vol_tnrt: 거래량 회전율
            - eps: EPS
            - bps: BPS
            - d250_hgpr: 250일 최고가
            - d250_hgpr_date: 250일 최고가 일자
            - d250_hgpr_vrss_prpr_rate: 250일 최고가 대비 현재가 비율
            - d250_lwpr: 250일 최저가
            - d250_lwpr_date: 250일 최저가 일자
            - d250_lwpr_vrss_prpr_rate: 250일 최저가 대비 현재가 비율
            - stck_dryy_hgpr: 주식 연중 최고가
            - dryy_hgpr_vrss_prpr_rate: 연중 최고가 대비 현재가 비율
            - dryy_hgpr_date: 연중 최고가 일자
            - stck_dryy_lwpr: 주식 연중 최저가
            - dryy_lwpr_vrss_prpr_rate: 연중 최저가 대비 현재가 비율
            - dryy_lwpr_date: 연중 최저가 일자
            - w52_hgpr: 52주일 최고가
            - w52_hgpr_vrss_prpr_ctrt: 52주일 최고가 대비 현재가 대비
            - w52_hgpr_date: 52주일 최고가 일자
            - w52_lwpr: 52주일 최저가
            - w52_lwpr_vrss_prpr_ctrt: 52주일 최저가 대비 현재가 대비
            - w52_lwpr_date: 52주일 최저가 일자
            - whol_loan_rmnd_rate: 전체 융자 잔고 비율
            - ssts_yn: 공매도가능여부
            - stck_shrn_iscd: 주식 단축 종목코드
            - fcam_cnnm: 액면가 통화명
            - cpfn_cnnm: 자본금 통화명
            - apprch_rate: 접근도
            - frgn_hldn_qty: 외국인 보유 수량
            - vi_cls_code: VI적용구분코드
            - ovtm_vi_cls_code: 시간외단일가VI적용구분코드
            - last_ssts_cntg_qty: 최종 공매도 체결 수량
            - invt_caful_yn: 투자유의여부
            - mrkt_warn_cls_code: 시장경고코드
            - short_over_yn: 단기과열여부
            - sltr_yn: 정리매매여부
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

    def fetch_oversea_price(self, symbol: str) -> dict:
        """해외주식현재가/해외주식 현재체결가
        Args:
            symbol (str): 종목코드
        Returns:
            dict: API 개발 가이드 참조
            - rsym: 실시간조회종목코드
        - output
            - zdiv: 소수점자리수
            - base: 전일종가
            - pvol: 전일거래량
            - last: 현재가
            - sign: 대비기호
            - diff: 대비
            - rate: 등락율
            - tvol: 거래량
            - tamt: 거래대금
            - ordy: 매수가능여부
        """
        path = "uapi/overseas-price/v1/quotations/price"
        url = f"{self.base_url}/{path}"

        # request header
        headers = {
           "content-type": "application/json",
           "authorization": self.access_token,
           "appKey": self.auth_config.app_key,
           "appSecret": self.auth_config.app_secret,
           "tr_id": "HHDFS00000300"
        }

        # query parameter
        params = {
            "AUTH": "",
            "EXCD": "NAS",
            "SYMB": symbol
        }
        resp = requests.get(url, headers=headers, params=params)
        return resp.json()
    
    def fetch_domestic_total_balance(self) -> pd.DataFrame:
        raw_domestic_stock = pd.DataFrame(self.fetch_domestic_balance()['output1'])
        raw_domestic_cash = pd.DataFrame(self.fetch_domestic_balance()['output2'])
        # CASH BALANCE
        # domestic_cash = pd.DataFrame(raw_domestic_cash['dnca_tot_amt'].rename('current_value')) # 오늘 구매한 주식에 대해서는 정산되지 않은 상태인 것 같다.
        domestic_cash = pd.DataFrame(raw_domestic_cash['prvs_rcdl_excc_amt'].rename('current_value'))
        domestic_cash['current_value'] = domestic_cash['current_value'].astype(float)
        domestic_cash['stock_nm'] = 'WON_DEPOSIT'
        domestic_cash['ticker'] = 'CASH'
        domestic_cash['current_price'] = 0
        domestic_cash['current_quantity'] = 0
        
        # STOCK BALANCE
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

    def fetch_oversea_total_balance(self) -> pd.DataFrame:
        raw_oversea_stock = pd.DataFrame(self.fetch_oversea_balance()['output1'])
        raw_oversea_cash = pd.DataFrame(self.fetch_oversea_present_balance(False)['output2'])
        
        # CASH BALANCE
        oversea_cash = pd.DataFrame(raw_oversea_cash['frcr_evlu_amt2'].rename('current_value'))
        oversea_cash['current_value'] = oversea_cash['current_value'].astype(float)
        oversea_cash['stock_nm'] = 'WON_DEPOSIT'
        oversea_cash['ticker'] = 'CASH'
        oversea_cash['current_price'] = 0
        oversea_cash['current_quantity'] = 0
        
        exchange_rate = raw_oversea_cash['frst_bltn_exrt'].astype(float)[0]

        # STOCK BALANCE
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

    def fetch_price(self, ticker: str) -> float:
        # 한국주식: 6글자의 숫자로 이루어짐
        if ticker.isdigit() and len(ticker) == 6:
            return float(self.fetch_domestic_price('J', ticker)['output']['stck_prpr'])
        return float(self.fetch_oversea_price(ticker)['output']['last']) * self.exchange_rate

    def create_domestic_order(self, transaction_type: str, ticker: str, price: int, ord_qty: int, ord_dvsn: str) -> dict:
        """국내주식주문/주식주문(현금)

        Args:
            side (str): _description_
            ticker (str): symbol
            price (int): 주문단가
            ord_qty (int): 주문주식수
            ord_dvsn (str): 주문구분
                - 00 : 지정가
                - 01 : 시장가
                - 02 : 조건부지정가
                - 03 : 최유리지정가
                - 04 : 최우선지정가
                - 05 : 장전 시간외 (08:20~08:40)
                - 06 : 장후 시간외 (15:30~16:00)
                - 07 : 시간외 단일가(16:00~18:00)
                - 08 : 자기주식
                - 09 : 자기주식S-Option
                - 10 : 자기주식금전신탁
                - 11 : IOC지정가 (즉시체결,잔량취소)
                - 12 : FOK지정가 (즉시체결,전량취소)
                - 13 : IOC시장가 (즉시체결,잔량취소)
                - 14 : FOK시장가 (즉시체결,전량취소)
                - 15 : IOC최유리 (즉시체결,잔량취소)
                - 16 : FOK최유리 (즉시체결,전량취소)
        Returns:
            dict: _description_
        """
        path = "uapi/domestic-stock/v1/trading/order-cash"
        url = f"{self.base_url}/{path}"

        tr_id = "TTTC0802U" if transaction_type == "buy" else "TTTC0801U"
        
        # 지정가 이외의 장전 시간외, 장후 시간외, 시장가 등 모든 주문구분의 경우 1주당 가격을 공란으로 비우지 않음 "0"으로 입력 권고
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

    def buy_domestic_stock(self, ticker: str, price: int, ord_qty: int, ord_dvsn: str) -> dict:
        return self.create_order(transaction_type='buy', ticker=ticker, price=price, ord_qty=ord_qty, ord_dvsn=ord_dvsn)
    
    def sell_domestic_stock(self, ticker: str, price: int, ord_qty: int, ord_dvsn: str) -> dict:
        return self.create_order(transaction_type='sell', ticker=ticker, price=price, ord_qty=ord_qty, ord_dvsn=ord_dvsn)
