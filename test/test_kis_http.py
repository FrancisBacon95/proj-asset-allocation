"""KIS HTTP 헬퍼 견고성 단위 테스트 (ARCH-005).

거래 없이 모킹된 응답으로 다음을 검증한다:
- _get/_post: timeout 인자 전달, raise_for_status 호출, RequestException 래핑
- _parse_json: JSON 파싱 실패 → KISAPIError
- _check_rt_cd: rt_cd != '0' → KISAPIError, rt_cd == '0' → 통과
- _get_json/_post_json: 정상 path + rt_cd 검증 path
- create_domestic_order: rt_cd != '0'에서도 raise하지 않고 dict 반환 (executor 호환)

실행: `uv run python -m test.test_kis_http`
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))

from src.kis.client import KISClient, KISAPIError, DEFAULT_REQUEST_TIMEOUT


def _make_client() -> KISClient:
    """KISClient.__init__의 인증·토큰 흐름을 우회한 인스턴스 생성."""
    c = KISClient.__new__(KISClient)
    c.acc_no = '00000000-01'
    c.acc_no_prefix = '00000000'
    c.acc_no_postfix = '01'
    c.mock = False
    c.base_url = "https://openapi.koreainvestment.com:9443"
    c.access_token = 'Bearer dummy'
    # auth_config는 _headers에서 .app_key/.app_secret만 참조
    c.auth_config = MagicMock()
    c.auth_config.app_key = 'KEY'
    c.auth_config.app_secret = 'SECRET'
    return c


def _ok_response(payload: dict, status: int = 200, headers: dict | None = None):
    """requests.Response 흉내 — json()/raise_for_status()/headers 모킹."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.json.return_value = payload
    resp.headers = headers or {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------- #
# 세트 1 — _get/_post: timeout·raise_for_status·RequestException                  #
# ---------------------------------------------------------------------------- #

def test_get_passes_default_timeout():
    """1-1. _get은 DEFAULT_REQUEST_TIMEOUT을 timeout 인자로 전달."""
    c = _make_client()
    with patch('src.kis.client.requests.get', return_value=_ok_response({'rt_cd': '0'})) as mock_get:
        c._get('uapi/path', 'TR_ID', {'k': 'v'})
    _, kwargs = mock_get.call_args
    assert kwargs.get('timeout') == DEFAULT_REQUEST_TIMEOUT, \
        f'_get은 timeout={DEFAULT_REQUEST_TIMEOUT} 강제, 실제 {kwargs.get("timeout")}'
    print('✅ 1-1 _get: timeout=DEFAULT_REQUEST_TIMEOUT 전달')


def test_post_passes_default_timeout():
    """1-2. _post도 timeout 인자 강제."""
    c = _make_client()
    with patch('src.kis.client.requests.post', return_value=_ok_response({'rt_cd': '0'})) as mock_post:
        c._post('uapi/path', 'TR_ID', {'k': 'v'})
    _, kwargs = mock_post.call_args
    assert kwargs.get('timeout') == DEFAULT_REQUEST_TIMEOUT
    print('✅ 1-2 _post: timeout=DEFAULT_REQUEST_TIMEOUT 전달')


def test_get_wraps_request_exception():
    """1-3. RequestException(연결 실패·타임아웃 등)은 KISAPIError로 래핑."""
    c = _make_client()
    with patch('src.kis.client.requests.get', side_effect=requests.ConnectionError('boom')):
        try:
            c._get('uapi/x', 'TR_ID', {})
        except KISAPIError as e:
            assert 'TR_ID' in str(e), '예외 메시지에 tr_id 포함돼야 함'
            assert 'uapi/x' in str(e), '예외 메시지에 path 포함돼야 함'
            assert isinstance(e.__cause__, requests.ConnectionError), 'cause 보존돼야 함'
            print('✅ 1-3 _get: RequestException → KISAPIError 래핑 (path/tr_id 컨텍스트 포함)')
            return
    raise AssertionError('KISAPIError가 발생하지 않음')


def test_get_wraps_http_error():
    """1-4. raise_for_status() HTTPError → KISAPIError."""
    c = _make_client()
    with patch('src.kis.client.requests.get', return_value=_ok_response({}, status=500)):
        try:
            c._get('uapi/x', 'TR_ID', {})
        except KISAPIError as e:
            assert e.status_code == 500
            assert 'http=500' in str(e)
            print('✅ 1-4 _get: HTTPError → KISAPIError (status_code=500 보존)')
            return
    raise AssertionError('KISAPIError가 발생하지 않음')


def test_post_wraps_http_error():
    """1-5. _post HTTP 비정상도 동일 패턴."""
    c = _make_client()
    with patch('src.kis.client.requests.post', return_value=_ok_response({}, status=503)):
        try:
            c._post('uapi/x', 'TR_ID', {})
        except KISAPIError as e:
            assert e.status_code == 503
            print('✅ 1-5 _post: HTTPError → KISAPIError (status_code=503 보존)')
            return
    raise AssertionError('KISAPIError가 발생하지 않음')


# ---------------------------------------------------------------------------- #
# 세트 2 — _parse_json / _check_rt_cd                                           #
# ---------------------------------------------------------------------------- #

def test_parse_json_wraps_decode_error():
    """2-1. resp.json()이 ValueError(JSONDecodeError)를 던지면 KISAPIError로 변환."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.side_effect = ValueError('not json')
    try:
        KISClient._parse_json(resp, path='uapi/x', tr_id='TR_ID')
    except KISAPIError as e:
        assert 'TR_ID' in str(e)
        assert isinstance(e.__cause__, ValueError)
        print('✅ 2-1 _parse_json: ValueError → KISAPIError 래핑')
        return
    raise AssertionError('KISAPIError가 발생하지 않음')


def test_check_rt_cd_passes_for_zero():
    """2-2. rt_cd == '0'은 통과."""
    KISClient._check_rt_cd({'rt_cd': '0', 'msg1': 'OK'}, path='uapi/x', tr_id='TR_ID')
    print('✅ 2-2 _check_rt_cd: rt_cd=0 통과')


def test_check_rt_cd_raises_for_nonzero():
    """2-3. rt_cd != '0'은 KISAPIError, rt_cd/msg1 attribute 보존."""
    try:
        KISClient._check_rt_cd(
            {'rt_cd': '1', 'msg1': '잘못된 요청'}, path='uapi/x', tr_id='TR_ID',
        )
    except KISAPIError as e:
        assert e.kis_rt_cd == '1'
        assert e.kis_msg == '잘못된 요청'
        assert 'rt_cd=1' in str(e)
        print('✅ 2-3 _check_rt_cd: rt_cd!=0 → KISAPIError (rt_cd·msg1 attribute 보존)')
        return
    raise AssertionError('KISAPIError가 발생하지 않음')


def test_check_rt_cd_raises_for_missing():
    """2-4. rt_cd 누락도 비정상 — 도메인 예외."""
    try:
        KISClient._check_rt_cd({'msg1': 'no rt_cd'}, path='uapi/x', tr_id='TR_ID')
    except KISAPIError:
        print('✅ 2-4 _check_rt_cd: rt_cd 누락 → KISAPIError')
        return
    raise AssertionError('KISAPIError가 발생하지 않음')


# ---------------------------------------------------------------------------- #
# 세트 3 — _get_json / _post_json 파이프라인                                       #
# ---------------------------------------------------------------------------- #

def test_get_json_returns_payload_when_rt_cd_zero():
    """3-1. _get_json: 정상 응답이면 payload dict 그대로 반환."""
    c = _make_client()
    with patch('src.kis.client.requests.get', return_value=_ok_response({'rt_cd': '0', 'output': {'x': 1}})):
        payload = c._get_json('uapi/x', 'TR_ID', {})
    assert payload['output'] == {'x': 1}
    print('✅ 3-1 _get_json: 정상 path → payload 반환')


def test_get_json_raises_when_rt_cd_nonzero():
    """3-2. _get_json: rt_cd != '0'이면 KISAPIError."""
    c = _make_client()
    with patch('src.kis.client.requests.get', return_value=_ok_response({'rt_cd': '7', 'msg1': 'fail'})):
        try:
            c._get_json('uapi/x', 'TR_ID', {})
        except KISAPIError as e:
            assert e.kis_rt_cd == '7'
            print('✅ 3-2 _get_json: rt_cd 비정상 → KISAPIError')
            return
    raise AssertionError('KISAPIError가 발생하지 않음')


def test_get_json_skips_validation_when_disabled():
    """3-3. _get_json(validate_rt_cd=False): 비정상 rt_cd여도 통과."""
    c = _make_client()
    with patch('src.kis.client.requests.get', return_value=_ok_response({'rt_cd': '7', 'msg1': 'fail'})):
        payload = c._get_json('uapi/x', 'TR_ID', {}, validate_rt_cd=False)
    assert payload['rt_cd'] == '7'
    print('✅ 3-3 _get_json(validate_rt_cd=False): 비정상 rt_cd도 통과')


def test_post_json_skips_validation_for_orders():
    """3-4. _post_json(validate_rt_cd=False): create_domestic_order 호환 — 실패 응답도 dict 반환."""
    c = _make_client()
    with patch('src.kis.client.requests.post', return_value=_ok_response({'rt_cd': '8', 'msg1': '주문 거부'})):
        payload = c._post_json('uapi/order', 'TR_ID', {}, validate_rt_cd=False)
    assert payload['rt_cd'] == '8'
    assert payload['msg1'] == '주문 거부'
    print('✅ 3-4 _post_json(validate_rt_cd=False): 실패 응답도 dict 반환 (executor 호환)')


# ---------------------------------------------------------------------------- #
# 세트 4 — create_domestic_order 회귀: 비정상 rt_cd에서도 raise하지 않음               #
# ---------------------------------------------------------------------------- #

def test_create_order_returns_dict_on_failure():
    """4-1. KIS가 rt_cd='1'(실패)을 줘도 create_domestic_order는 dict 반환.

    executor는 response['rt_cd']로 is_success를 결정하므로, 실패 응답에서 raise하면
    Slack/BigQuery 기록이 끊긴다. 의도적으로 validate_rt_cd=False.
    """
    c = _make_client()
    failure_payload = {'rt_cd': '1', 'msg1': '주문 거부됨'}
    with patch.object(c, 'issue_hashkey', return_value='HASH'):
        with patch('src.kis.client.requests.post', return_value=_ok_response(failure_payload)):
            result = c.create_domestic_order(
                transaction_type='buy', ticker='005930',
                ord_qty=1, ord_dvsn='01',
            )
    assert result['rt_cd'] == '1'
    assert result['msg1'] == '주문 거부됨'
    print('✅ 4-1 create_domestic_order: rt_cd=1여도 raise 안 함 (executor 호환)')


# ---------------------------------------------------------------------------- #
# Runner                                                                       #
# ---------------------------------------------------------------------------- #
if __name__ == '__main__':
    test_get_passes_default_timeout()
    test_post_passes_default_timeout()
    test_get_wraps_request_exception()
    test_get_wraps_http_error()
    test_post_wraps_http_error()
    test_parse_json_wraps_decode_error()
    test_check_rt_cd_passes_for_zero()
    test_check_rt_cd_raises_for_nonzero()
    test_check_rt_cd_raises_for_missing()
    test_get_json_returns_payload_when_rt_cd_zero()
    test_get_json_raises_when_rt_cd_nonzero()
    test_get_json_skips_validation_when_disabled()
    test_post_json_skips_validation_for_orders()
    test_create_order_returns_dict_on_failure()
    print('\n🎉 모든 ARCH-005 HTTP 견고성 테스트 통과')
