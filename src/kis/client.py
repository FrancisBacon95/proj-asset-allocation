"""한국투자증권 REST API 클라이언트.

핵심 설계:
- HTTP 견고성 (ARCH-005): 모든 KIS 호출은 단일 헬퍼 경로(`_request` → `_parse_json`
  → `_check_rt_cd`)를 통과한다. 네트워크 timeout, raise_for_status, JSON 파싱 실패,
  KIS rt_cd 비정상 모두 `KISAPIError` 도메인 예외로 통일. 민감 정보(헤더·앱키)는
  노출하지 않고 path/tr_id/HTTP status/rt_cd/msg1만 보존.

- 토큰 캐시 (ARCH-011): pickle 폐기, JSON 형식(`kis_token_*.json`). 캐시에는
  access_token + expires_at(epoch)만 포함, app_key/app_secret은 캐시에서 제외.
  손상된 캐시는 자동 폐기 → 재발급 트리거. 파일 권한 0600 (best-effort).

- 계좌 분기 (postfix 기반): `is_irp()` (= `_is_pension()` alias) — IRP(='29')만
  KIS 퇴직연금 전용 엔드포인트(TTTC2208R, TTTC0503R) 사용. ISA('01')와 연금저축
  PPA('22')은 일반 위탁 엔드포인트 공유. 자세한 내용은 docs/kis_cash_guide.md §4.2.

- 응답 형식 정규화: `_normalize_output1/output2`로 ISA(list)/IRP(dict) 응답 차이를
  통일. `_require_int / _optional_int`로 silent failure 방지.
"""
import os
import requests
import json
from typing import Optional
from zoneinfo import ZoneInfo
from datetime import date, datetime

import pandas as pd
from src.config.env import PROJECT_ROOT, load_kis_auth_config
from src.logger import get_logger, log_method_call

logger = get_logger(__name__)

# nrcvb_buy_amt 조회용 더미 ticker. inquire-psbl-order는 ticker 인자가 필수이지만
# nrcvb_buy_amt 자체는 종목 무관 계좌 단위 상수임이 실험으로 확정됨 (test/dump_isa_orderable.py).
DUMMY_TICKER_FOR_ORDERABLE_CASH = '005930'  # 삼성전자, 상장폐지 위험 사실상 없음


# === KIS HTTP 견고성 (ARCH-005) =========================================== #
# 모든 KIS API 호출은 본 모듈의 _request/_parse_json/_check_rt_cd 경로를
# 통과해야 한다. 무한 hang 방지(timeout), HTTP 비정상 가시화(raise_for_status),
# 응답 형식 가드(JSON 파싱), 비즈니스 코드 검증(rt_cd) 네 단계를 일관되게 적용.

DEFAULT_REQUEST_TIMEOUT = 10  # seconds. 외부 API 응답 대기 상한.


class KISAPIError(RuntimeError):
    """KIS API 호출 실패 도메인 예외.

    네트워크 오류·HTTP 비정상·JSON 파싱 실패·KIS rt_cd 비정상 모두 본 예외로 통일한다.
    민감 정보(헤더·앱 키 등)는 노출하지 않고, 운영 진단에 필요한 endpoint/tr_id/
    HTTP status/rt_cd/msg1만 attribute로 보존한다.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Optional[str] = None,
        tr_id: Optional[str] = None,
        status_code: Optional[int] = None,
        kis_rt_cd: Optional[str] = None,
        kis_msg: Optional[str] = None,
    ) -> None:
        self.path = path
        self.tr_id = tr_id
        self.status_code = status_code
        self.kis_rt_cd = kis_rt_cd
        self.kis_msg = kis_msg
        ctx_parts = []
        if path:
            ctx_parts.append(f"path={path}")
        if tr_id:
            ctx_parts.append(f"tr_id={tr_id}")
        if status_code is not None:
            ctx_parts.append(f"http={status_code}")
        if kis_rt_cd is not None:
            ctx_parts.append(f"rt_cd={kis_rt_cd}")
        if kis_msg:
            ctx_parts.append(f"msg={kis_msg}")
        ctx = f" [{', '.join(ctx_parts)}]" if ctx_parts else ""
        super().__init__(f"{message}{ctx}")


# === KIS 응답 안전 추출 유틸 =============================================== #
# 비즈니스 로직(메서드 본문)을 깨끗이 유지하기 위한 모듈 레벨 사적 헬퍼.
# - _require_int / _optional_int: silent failure 방지 + 선택 필드 폴백 명시
# - _normalize_output2 / _normalize_output1: ISA(list)/IRP(dict) 응답 형식 통일

def _require_int(d: dict, key: str) -> int:
    """KIS 응답의 필수 정수 필드. 누락·빈문자열·변환실패 시 KeyError/ValueError로 즉시 실패.

    silent failure 방지 — 빈 응답이면 명시적으로 폭발해 운영자가 발견하도록.
    """
    return int(d[key])


def _optional_int(d: dict, key: str, default: int = 0) -> int:
    """KIS 응답의 선택 정수 필드. 누락·빈문자열·변환실패 시 default 반환."""
    v = d.get(key, default)
    if v in (None, ''):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _normalize_output2(raw) -> dict:
    """잔고조회 output2 형식 정규화 (ISA list / IRP dict 모두 dict 단일로)."""
    if isinstance(raw, list):
        return raw[0] if raw else {}
    return raw if isinstance(raw, dict) else {}


def _normalize_output1(raw) -> list:
    """잔고조회 output1 형식 정규화 (단일 dict면 list 감싸기, list면 그대로)."""
    if isinstance(raw, list):
        return raw
    return [raw] if raw else []


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

        # 토큰 캐시: pickle('.dat')에서 JSON('.json')으로 전환 (ARCH-011).
        # 기존 .dat 파일은 자연스럽게 미사용으로 폐기 (gitignore: kis_token_* 모두 매치).
        self.token_file = PROJECT_ROOT / f'kis_token_{account_type}.json'
        self.access_token = None

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
        """저장된 토큰이 유효한지 확인한다 (JSON 캐시).

        다음 중 하나라도 해당되면 False (= 재발급 필요):
        - 토큰 파일 부재
        - JSON 파싱 실패 (예: 기존 pickle 파일·손상된 캐시)
        - 만료 시각 지남 또는 expires_at 형식 비정상

        ARCH-011: 캐시에는 access_token + expires_at만 저장.
        app_key/app_secret 모두 캐시·검증에 사용하지 않는다 (디스크 노출 최소화).
        필요 시 .env에서 self.auth_config로 매번 다시 로드.
        """
        if not self.token_file.exists():
            return False
        try:
            with self.token_file.open('r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # 손상된 캐시 또는 기존 pickle 파일 — 무효 처리하여 재발급 트리거
            logger.warning('토큰 캐시 파싱 실패. 재발급 진행: %s', self.token_file)
            return False
        expires_at = data.get('expires_at')
        if not isinstance(expires_at, int):
            return False
        return int(datetime.now().timestamp()) < expires_at

    def issue_access_token(self) -> None:
        """OAuth 액세스 토큰을 발급받아 JSON 파일에 저장한다.

        ARCH-011: 캐시에는 access_token + expires_at(epoch)만 저장.
        app_key/app_secret 모두 의도적으로 제외 (디스크 노출 최소화).
        """
        path = "oauth2/tokenP"
        url = f"{self.base_url}/{path}"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.auth_config.app_key,
            "appsecret": self.auth_config.app_secret,
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=DEFAULT_REQUEST_TIMEOUT)
        except requests.RequestException as e:
            raise KISAPIError(
                f"KIS 토큰 발급 요청 실패: {type(e).__name__}", path=path,
            ) from e
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise KISAPIError(
                "KIS 토큰 발급 HTTP 비정상 응답", path=path, status_code=resp.status_code,
            ) from e
        resp_data = self._parse_json(resp, path=path)
        if 'access_token' not in resp_data:
            raise KISAPIError(
                "KIS 토큰 발급 응답에 access_token 부재", path=path,
                status_code=resp.status_code,
            )
        self.access_token = f'Bearer {resp_data["access_token"]}'

        # 만료 시각을 epoch(초)로 변환해 저장 (유효성 검사에 사용)
        timezone = ZoneInfo('Asia/Seoul')
        expires_at = int(
            datetime.strptime(resp_data['access_token_token_expired'], '%Y-%m-%d %H:%M:%S')
            .replace(tzinfo=timezone).timestamp()
        )
        cache = {
            'access_token': resp_data['access_token'],
            'expires_at': expires_at,
            # app_key/app_secret 모두 캐시 안 함 — 필요 시 .env에서 self.auth_config로 다시 로드
        }
        self._write_token_cache(cache)

    def _write_token_cache(self, cache: dict) -> None:
        """토큰 캐시를 JSON으로 기록하고 파일 권한을 0600(소유자 RW만)로 제한한다."""
        self.token_file.write_text(json.dumps(cache, indent=2), encoding='utf-8')
        try:
            os.chmod(self.token_file, 0o600)
        except OSError:
            # Windows 등 chmod 미지원 환경에서도 동작 (권한 제한은 best-effort)
            logger.debug('토큰 파일 권한 0600 설정 실패 (OS 미지원 가능)')

    def load_access_token(self) -> None:
        """파일에서 액세스 토큰을 읽어 self.access_token에 설정한다."""
        with self.token_file.open('r', encoding='utf-8') as f:
            data = json.load(f)
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
        try:
            resp = requests.post(
                url, headers=headers, data=json.dumps(data),
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise KISAPIError(
                f"KIS 해시키 발급 요청 실패: {type(e).__name__}", path=path,
            ) from e
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise KISAPIError(
                "KIS 해시키 발급 HTTP 비정상 응답", path=path, status_code=resp.status_code,
            ) from e
        payload = self._parse_json(resp, path=path)
        if 'HASH' not in payload:
            raise KISAPIError(
                "KIS 해시키 응답에 HASH 부재", path=path, status_code=resp.status_code,
            )
        return payload["HASH"]

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

    # ------------------------------------------------------------------ #
    # HTTP 헬퍼 (ARCH-005)                                                  #
    # ------------------------------------------------------------------ #
    # 외부 호출은 모두 _get/_post를 통과한다.
    # - timeout: DEFAULT_REQUEST_TIMEOUT 강제 → 무한 hang 방지
    # - raise_for_status: 4xx/5xx를 HTTPError로 가시화
    # - RequestException(타임아웃·DNS·연결 거부 등): KISAPIError로 감쌈
    # 응답 본문 파싱·rt_cd 검증은 _get_json/_post_json 또는 호출부에서 수행.

    def _get(self, path: str, tr_id: str, params: dict, **extra) -> requests.Response:
        """공통 GET 요청 헬퍼. timeout + raise_for_status 적용, 실패 시 KISAPIError."""
        url = f"{self.base_url}/{path}"
        try:
            resp = requests.get(
                url,
                headers=self._headers(tr_id, **extra),
                params=params,
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise KISAPIError(
                f"KIS GET 요청 실패: {type(e).__name__}", path=path, tr_id=tr_id,
            ) from e
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise KISAPIError(
                "KIS HTTP 비정상 응답", path=path, tr_id=tr_id, status_code=resp.status_code,
            ) from e
        return resp

    def _post(self, path: str, tr_id: str, data: dict, **extra) -> requests.Response:
        """공통 POST 요청 헬퍼. timeout + raise_for_status 적용, 실패 시 KISAPIError."""
        url = f"{self.base_url}/{path}"
        try:
            resp = requests.post(
                url,
                headers=self._headers(tr_id, **extra),
                data=json.dumps(data),
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise KISAPIError(
                f"KIS POST 요청 실패: {type(e).__name__}", path=path, tr_id=tr_id,
            ) from e
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise KISAPIError(
                "KIS HTTP 비정상 응답", path=path, tr_id=tr_id, status_code=resp.status_code,
            ) from e
        return resp

    @staticmethod
    def _parse_json(resp: requests.Response, *, path: str, tr_id: Optional[str] = None) -> dict:
        """응답 JSON 파싱. 형식 비정상 시 KISAPIError로 변환."""
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise KISAPIError(
                "KIS 응답 JSON 파싱 실패", path=path, tr_id=tr_id,
                status_code=resp.status_code,
            ) from e

    @staticmethod
    def _check_rt_cd(payload: dict, *, path: str, tr_id: Optional[str] = None) -> None:
        """KIS 응답 rt_cd '0' 검증. 비정상이면 KISAPIError.

        rt_cd가 부재하는 응답(oauth2/tokenP, hashkey 등)에는 호출하지 않는다.
        """
        rt_cd = payload.get('rt_cd')
        if rt_cd != '0':
            raise KISAPIError(
                "KIS 응답 비정상 (rt_cd != 0)",
                path=path, tr_id=tr_id,
                kis_rt_cd=str(rt_cd) if rt_cd is not None else None,
                kis_msg=payload.get('msg1'),
            )

    def _get_json(
        self,
        path: str,
        tr_id: str,
        params: dict,
        *,
        validate_rt_cd: bool = True,
        **extra,
    ) -> dict:
        """GET → JSON 파싱 → (옵션) rt_cd 검증까지 일괄 처리한 편의 메서드.

        헤더(tr_cont 페이지네이션 등)가 필요한 호출은 _get을 직접 사용.
        """
        resp = self._get(path, tr_id, params, **extra)
        payload = self._parse_json(resp, path=path, tr_id=tr_id)
        if validate_rt_cd:
            self._check_rt_cd(payload, path=path, tr_id=tr_id)
        return payload

    def _post_json(
        self,
        path: str,
        tr_id: str,
        data: dict,
        *,
        validate_rt_cd: bool = True,
        **extra,
    ) -> dict:
        """POST → JSON 파싱 → (옵션) rt_cd 검증까지 일괄 처리한 편의 메서드."""
        resp = self._post(path, tr_id, data, **extra)
        payload = self._parse_json(resp, path=path, tr_id=tr_id)
        if validate_rt_cd:
            self._check_rt_cd(payload, path=path, tr_id=tr_id)
        return payload

    # ------------------------------------------------------------------ #
    # 계좌 유형 분기 헬퍼 (ISA·연금저축 vs IRP)                                   #
    # ------------------------------------------------------------------ #
    # acc_no_postfix '01' = ISA(일반/위탁), '22' = 연금저축(PPA — ISA와 동일 처리),
    # '29' = IRP(개인형 퇴직연금 — KIS 퇴직연금 전용 엔드포인트 사용).
    # IRP는 모의투자 미지원이므로 잔고/매수가능 TR_ID 모두 실전 단일.

    def is_irp(self) -> bool:
        """IRP(개인형 퇴직연금) 계좌 여부. acc_no_postfix == '29'.

        public 메서드 — 외부 모듈(allocation.py, main.py)에서 IRP 분기에 사용.
        KIS 퇴직연금 전용 엔드포인트(TTTC2208R, TTTC0503R 등)는 IRP에만 응답.
        연금저축(PPA, postfix='22')은 ISA와 동일한 일반 위탁 엔드포인트로 처리.
        """
        return self.acc_no_postfix == '29'

    def _is_pension(self) -> bool:
        """KIS 퇴직연금 전용 엔드포인트 분기용 alias of is_irp() (내부 사용).

        client.py 내부 분기에서 의미를 강조하기 위해 별도 이름 유지. 동작은 is_irp()와 동일.
        """
        return self.is_irp()

    def _balance_tr_id(self) -> str:
        """국내주식 잔고조회 TR_ID. IRP는 TTTC2208R(모의 미지원), ISA·연금저축은 TTTC8434R/VTTC8434R."""
        if self._is_pension():
            return 'TTTC2208R'
        return 'VTTC8434R' if self.mock else 'TTTC8434R'

    def _orderable_tr_id(self) -> str:
        """국내주식 매수가능조회 TR_ID. IRP는 TTTC0503R(모의 미지원), ISA·연금저축은 TTTC8908R."""
        if self._is_pension():
            return 'TTTC0503R'
        return 'TTTC8908R'

    def _balance_url(self) -> str:
        """국내주식 잔고조회 URL path. IRP는 /pension/ 경로, ISA·연금저축은 일반 경로."""
        if self._is_pension():
            return 'uapi/domestic-stock/v1/trading/pension/inquire-balance'
        return 'uapi/domestic-stock/v1/trading/inquire-balance'

    def _orderable_url(self) -> str:
        """국내주식 매수가능조회 URL path. IRP는 /pension/ 경로, ISA·연금저축은 일반 경로."""
        if self._is_pension():
            return 'uapi/domestic-stock/v1/trading/pension/inquire-psbl-order'
        return 'uapi/domestic-stock/v1/trading/inquire-psbl-order'

    def _order_tr_id(self, transaction_type: str) -> str:
        """매수/매도 주문 TR_ID. ISA·연금저축·IRP 동일하게 추정 (공식 doc 없음).

        KIS 공식 API 목록(`kis_api_docs/md/한투_API_목록.md`)에 IRP 전용 주문 API가
        부재. 일반 주식주문(현금) 엔드포인트로 호환되는 것으로 추정. 만약 미래에
        IRP 전용 TR_ID가 발견되면 본 헬퍼에서 분기.
        """
        if transaction_type == 'buy':
            return 'TTTC0802U'
        elif transaction_type == 'sell':
            return 'TTTC0801U'
        raise ValueError(f"transaction_type must be 'buy' or 'sell', got: {transaction_type!r}")

    # ------------------------------------------------------------------ #
    # 잔고 조회                                                             #
    # ------------------------------------------------------------------ #

    @log_method_call
    def fetch_domestic_cash_breakdown(self) -> dict:
        """국내 계좌 예수금 D+0/D+1/D+2 + 평가금액 요약을 반환한다.

        진단·검증 목적. KIS 잔고조회(inquire-balance) output2 응답에서
        매수 가능 현금 및 총평가금액 산정에 직접 관계되는 필드를 추출한다.

        KIS 공식 정의: tot_evlu_amt = scts_evlu_amt + prvs_rcdl_excc_amt
        즉 "총평가금액 = 유가증권 평가금액 + D+2 예수금"이며, 본 시스템의
        리밸런싱 기준 총자산도 이 식과 일치해야 한다.

        Returns:
            dict: 다음 정수 필드를 갖는 dict.
                - dnca_tot_amt: D+0 예수금 (현재 결제 완료 잔액)
                - nxdy_excc_amt: D+1 예수금 (내일 시점 예상 잔액)
                - prvs_rcdl_excc_amt: D+2 예수금 (모든 미정산 청산 후 잔액)
                - thdt_buy_amt: 금일 매수 체결액
                - thdt_sll_amt: 금일 매도 체결액
                - scts_evlu_amt: 유가증권 평가금액 (보유 종목 합계)
                - tot_evlu_amt: 총평가금액 (= scts_evlu_amt + prvs_rcdl_excc_amt)

        참고: docs/kis_cash_guide.md
        """
        output2 = _normalize_output2(self._domestic_balance_page()['output2'])
        return {
            # 필수 — silent failure 방지 (누락 시 KeyError로 즉시 실패)
            'dnca_tot_amt':       _require_int(output2, 'dnca_tot_amt'),
            'nxdy_excc_amt':      _require_int(output2, 'nxdy_excc_amt'),
            'prvs_rcdl_excc_amt': _require_int(output2, 'prvs_rcdl_excc_amt'),
            'scts_evlu_amt':      _require_int(output2, 'scts_evlu_amt'),
            'tot_evlu_amt':       _require_int(output2, 'tot_evlu_amt'),
            # 선택 — 거래 없는 날 부재 가능
            'thdt_buy_amt':       _optional_int(output2, 'thdt_buy_amt'),
            'thdt_sll_amt':       _optional_int(output2, 'thdt_sll_amt'),
        }

    @log_method_call
    def fetch_domestic_cash_balance(self) -> int:
        """국내 계좌 매수 가능 현금을 T+2 예수금 기준으로 반환한다.

        prvs_rcdl_excc_amt(D+2 예수금)는 KIS API가 보고하는
        "T+2 시점에 결제 완료되어 있을 잔액"이다. 이전·오늘 매도의 미정산 대금이
        모두 반영되므로, 이를 매수에 활용해야 자본이 비효율적으로 묶이지 않는다.

        진단을 위해 D+0/D+1/D+2 및 금일 체결 금액을 함께 INFO 로그로 남긴다.

        참고: docs/kis_cash_guide.md
        """
        b = self.fetch_domestic_cash_breakdown()
        logger.info(
            'cash breakdown: D+0=%s, D+1=%s, D+2=%s, today_buy=%s, today_sell=%s',
            f'{b["dnca_tot_amt"]:,}',
            f'{b["nxdy_excc_amt"]:,}',
            f'{b["prvs_rcdl_excc_amt"]:,}',
            f'{b["thdt_buy_amt"]:,}',
            f'{b["thdt_sll_amt"]:,}',
        )
        return b['prvs_rcdl_excc_amt']

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
            output['output1'].extend(_normalize_output1(data['output1']))
            output['output2'].append(_normalize_output2(data['output2']))
            # tr_cont가 'D' 또는 'E'이면 마지막 페이지
            if data['tr_cont'] in ('D', 'E'):
                break
            fk100, nk100 = data['ctx_area_fk100'], data['ctx_area_nk100']
        return output

    @log_method_call
    def _domestic_balance_page(self, ctx_area_fk100: str = "", ctx_area_nk100: str = "") -> dict:
        """국내 잔고 단일 페이지를 조회한다. 페이지네이션 연속 키를 포함해 반환한다.

        ISA·연금저축: TTTC8434R(VTTC8434R 모의), IRP: TTTC2208R(모의 미지원).
        URL/TR_ID/params 모두 계좌 유형에 따라 분기된다.
        - IRP는 ACCA_DVSN_CD/INQR_DVSN='00' 필수, ISA 전용 파라미터 부재.
        - ISA·연금저축은 acc_no_postfix 그대로 + ISA 전용 파라미터 셋 사용.
        """
        if self._is_pension():
            # ACNT_PRDT_CD는 acc_no_postfix 그대로 사용. KIS doc의 "29"는 예시일 뿐.
            # IRP는 postfix='29'를 그대로 보내야 정상 응답.
            params = {
                'CANO': self.acc_no_prefix,
                'ACNT_PRDT_CD': self.acc_no_postfix,
                'ACCA_DVSN_CD': '00',          # 적립금구분(전체)
                'INQR_DVSN': '00',             # 조회구분(전체)
                'CTX_AREA_FK100': ctx_area_fk100,
                'CTX_AREA_NK100': ctx_area_nk100,
            }
        else:
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
                'CTX_AREA_NK100': ctx_area_nk100,
            }
        path = self._balance_url()
        tr_id = self._balance_tr_id()
        res = self._get(path, tr_id, params)
        data = self._parse_json(res, path=path, tr_id=tr_id)
        self._check_rt_cd(data, path=path, tr_id=tr_id)
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
        path = "uapi/overseas-stock/v1/trading/inquire-balance"
        res = self._get(path, tr_id, params)
        data = self._parse_json(res, path=path, tr_id=tr_id)
        self._check_rt_cd(data, path=path, tr_id=tr_id)
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
        return self._get_json(
            "uapi/overseas-stock/v1/trading/inquire-present-balance", tr_id, params,
        )

    @log_method_call
    def fetch_domestic_total_balance(self) -> pd.DataFrame:
        """국내 전체 잔고(주식 + 현금)를 단일 DataFrame으로 반환한다.

        현금은 ticker='CASH', stock_nm='WON_DEPOSIT'으로 행을 추가해 통합한다.
        """
        # columns 인자로 스키마를 명시 — 보유종목이 0개여도 컬럼 셀렉션 안전.
        # 또한 PPA output1에는 'prpr'(현재가)이 없어 누락되는데, columns 인자로 NaN 컬럼이 생성되고
        # 아래 fillna(0)으로 정리한다. planner는 fetch_price()로 ticker별 현재가를 다시 채우므로 0이어도 무해.
        raw_domestic_stock = pd.DataFrame(
            self.fetch_domestic_stock_balance()['output1'],
            columns=['pdno', 'prdt_name', 'hldg_qty', 'prpr', 'evlu_amt'],
        )
        # PPA에서 누락 가능한 prpr만 0으로 채움. 다른 컬럼은 PPA에도 존재(researcher 보고).
        raw_domestic_stock['prpr'] = raw_domestic_stock['prpr'].fillna(0)
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

        ISA·연금저축: TTTC8908R, IRP: TTTC0503R(모의 미지원).
        URL/params 모두 계좌 유형에 따라 분기된다.
        - IRP는 ACCA_DVSN_CD='00' 필수.
        - ISA·연금저축은 OVRS_ICLD_YN='N' 사용. CMA_EVLU_AMT_ICLD_YN은 양 doc 모두 Required.

        Args:
            ord_dvsn (str): 주문구분 (01: 시장가, 00: 지정가)
            price (int): 지정가 주문 시 주문단가. 시장가(ord_dvsn='01')일 때는 무시된다.
        """
        # ACNT_PRDT_CD는 acc_no_postfix 그대로 사용. KIS doc "29"는 예시 (실측 확인).
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'PDNO': ticker,
            'ORD_UNPR': price if ord_dvsn != '01' else '',  # 시장가이면 빈 문자열
            'ORD_DVSN': ord_dvsn,
            'CMA_EVLU_AMT_ICLD_YN': 'Y',  # 양 doc 모두 Required
        }
        if self._is_pension():
            params['ACCA_DVSN_CD'] = '00'   # IRP 적립금구분(전체)
        else:
            params['OVRS_ICLD_YN'] = 'N'    # ISA·연금저축 전용 (IRP doc에 없음)
        return self._get_json(self._orderable_url(), self._orderable_tr_id(), params)['output']

    @log_method_call
    def fetch_buy_orderable_cash(self) -> int:
        """미수 없는 매수 가능 한도를 반환한다.

        ISA·연금저축(PPA): inquire-psbl-order의 nrcvb_buy_amt(미수없는 매수 가능 금액)를 사용.
        IRP: 응답에 nrcvb_buy_amt가 없으므로 max_buy_amt를 사용.
            IRP는 미수 거래가 법적으로 불가능하므로 max_buy_amt가 의미상
            ISA의 nrcvb_buy_amt와 동등(=안전한 매수 한도)이다.

        nrcvb_buy_amt(또는 max_buy_amt) 자체가 종목 무관 계좌 단위 상수임이
        실험으로 확인되어 더미 ticker로 호출.

        fetch_domestic_cash_balance(D+2 예수금)와는 별개 개념이다:
        - 잔고/총자산 산정 → fetch_domestic_cash_balance (prvs_rcdl_excc_amt, KIS tot_evlu_amt 정의 일치)
        - 실제 매수 한도 → 본 함수

        참고: docs/kis_cash_guide.md
        """
        enable = self.fetch_domestic_enable_buy(
            ticker=DUMMY_TICKER_FOR_ORDERABLE_CASH, ord_dvsn='01',
        )
        if self._is_pension():
            return int(enable['max_buy_amt'])
        return int(enable['nrcvb_buy_amt'])

    @log_method_call
    def fetch_domestic_enable_sell(self, ticker: str) -> dict:
        """국내주식 매도 가능 수량을 조회한다."""
        params = {
            'CANO': self.acc_no_prefix,
            'ACNT_PRDT_CD': self.acc_no_postfix,
            'PDNO': ticker,
        }
        return self._get_json(
            "uapi/domestic-stock/v1/trading/inquire-psbl-sell", "TTTC8408R", params,
        )['output']

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
        return self._get_json(
            "uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", params,
        )

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
        return self._get_json(
            "uapi/overseas-price/v1/quotations/price", "HHDFS00000300", params,
        )

    @log_method_call
    def fetch_price(self, ticker: str) -> float:
        """국내(KRX) 종목의 현재가(원)를 반환한다.

        ticker는 KRX 단축코드(6자리 영숫자, 영문자 섞인 신규 ETF/ETN 코드 포함)를 그대로
        넘긴다. 유효하지 않은 코드면 KIS API가 에러를 반환한다.
        """
        return float(self.fetch_domestic_price('J', ticker)['output']['stck_prpr'])

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
        return self._get_json(
            "uapi/domestic-stock/v1/quotations/chk-holiday", "CTCA0903R", params,
            custtype="P",
        )['output']

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

        TR_ID는 `_order_tr_id` 헬퍼로 결정 — 현재 ISA·PPA·IRP 모두 동일 추정
        (TTTC0802U 매수 / TTTC0801U 매도). KIS 공식 API 목록에 퇴직연금 전용 주문 API
        부재. 미래에 PPA 전용 TR_ID가 발견되면 헬퍼만 수정.

        ACNT_PRDT_CD는 acc_no_postfix 그대로 (ISA='01'/PPA='22'/IRP='29').
        IRP에서는 잔고/매수가능조회와 일관되게 `ACCA_DVSN_CD='00'`을 추가
        (보수적 — _is_pension() 분기와 동일 패턴). PPA(연금저축)는 ISA로 처리되므로 추가 안 함.

        Args:
            transaction_type (str): 'buy' (매수) 또는 'sell' (매도)
            ord_dvsn (str): 주문구분 (00: 지정가, 01: 시장가, ...)
            price (int): 지정가 주문 시 주문단가. 시장가(ord_dvsn='01')이면 무시된다.
        """
        tr_id = self._order_tr_id(transaction_type)
        unpr = "0" if ord_dvsn == "01" else str(price)  # 시장가이면 단가를 "0"으로 전달
        data = {
            "CANO": self.acc_no_prefix,
            "ACNT_PRDT_CD": self.acc_no_postfix,
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(ord_qty),
            "ORD_UNPR": unpr,
        }
        if self._is_pension():
            # IRP 매수가능조회와 일관되게 ACCA_DVSN_CD='00' 추가 (보수적)
            data["ACCA_DVSN_CD"] = "00"
        # 주문 API는 request body 위변조 방지를 위해 해시키 서명이 필수
        hashkey = self.issue_hashkey(data)
        # rt_cd != '0'이어도 raise하지 않는다 — executor가 is_success=False로 기록해야
        # 부분 실패가 영속 저장소·Slack에 가시화된다 (ARCH-008과 일관).
        return self._post_json(
            "uapi/domestic-stock/v1/trading/order-cash", tr_id, data,
            validate_rt_cd=False, custtype="P", hashkey=hashkey,
        )