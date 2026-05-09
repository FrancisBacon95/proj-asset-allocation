"""KIS 토큰 캐시 단위 테스트 (ARCH-011: pickle→JSON, app_secret 제거).

검증:
- check_access_token: 다양한 invalid 케이스에서 False 반환 (재발급 트리거)
- issue_access_token: JSON으로 저장 + access_token/expires_at/app_key만 포함, app_secret은 부재
- load_access_token: JSON에서 access_token 정상 로드
- 손상된 캐시(기존 pickle 등) 자동 폐기 → 재발급 흐름

실행: `uv run python -m test.test_kis_token_cache`
"""
import sys
import json
import pickle
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))


def _make_client_with_temp_token_file(app_key: str = 'TEST_APP_KEY', app_secret: str = 'TEST_APP_SECRET'):
    """KISClient.__init__를 우회한 인스턴스 + 임시 토큰 파일 경로 주입."""
    from src.kis.client import KISClient
    c = KISClient.__new__(KISClient)
    c.auth_config = MagicMock()
    c.auth_config.app_key = app_key
    c.auth_config.app_secret = app_secret
    c.acc_no_postfix = '01'
    c.acc_no_prefix = '00000000'
    c.mock = False
    c.access_token = None
    # 임시 디렉터리에 토큰 파일 생성
    tmpdir = tempfile.mkdtemp(prefix='kis_token_test_')
    c.token_file = Path(tmpdir) / 'kis_token_TEST.json'
    return c


# ---------------------------------------------------------------------------- #
# check_access_token — invalid 케이스                                           #
# ---------------------------------------------------------------------------- #

def test_check_access_token_returns_false_when_file_missing():
    """토큰 파일 부재 시 False (재발급 트리거)."""
    c = _make_client_with_temp_token_file()
    assert not c.token_file.exists()
    assert c.check_access_token() is False
    print('✅ check_access_token: 파일 부재 → False')


def test_check_access_token_returns_false_for_corrupted_json():
    """JSON 파싱 실패 시 False (손상 캐시 자동 폐기)."""
    c = _make_client_with_temp_token_file()
    c.token_file.write_text('{ this is not valid json !!!', encoding='utf-8')
    assert c.check_access_token() is False
    print('✅ check_access_token: 손상된 JSON → False (재발급 트리거)')


def test_check_access_token_returns_false_for_legacy_pickle_cache():
    """기존 pickle(.dat 마이그레이션 케이스) 파일도 무효 처리 → 재발급."""
    c = _make_client_with_temp_token_file()
    # pickle 바이너리는 JSON 파싱이 무조건 실패하므로 자동 폐기
    pickle_payload = pickle.dumps({
        'access_token': 'old_token',
        'app_key': 'TEST_APP_KEY',
        'app_secret': 'OLD_SECRET',
        'timestamp': 9999999999,
    })
    c.token_file.write_bytes(pickle_payload)
    assert c.check_access_token() is False, '기존 pickle 캐시는 자동 무효 처리돼야 함 (마이그레이션)'
    print('✅ check_access_token: 기존 pickle 캐시 → False (자동 마이그레이션)')


def test_check_access_token_returns_false_when_expired():
    """만료 시각이 지나면 False."""
    c = _make_client_with_temp_token_file()
    c.token_file.write_text(json.dumps({
        'access_token': 'tok',
        'expires_at': 100,  # 1970년 epoch — 영구 만료
    }), encoding='utf-8')
    assert c.check_access_token() is False
    print('✅ check_access_token: 만료 시각 지남 → False')


def test_check_access_token_returns_false_when_expires_at_missing_or_invalid():
    """expires_at이 None/문자열 등 비정수면 False (잘못된 캐시 구조)."""
    c = _make_client_with_temp_token_file()
    for bad in [None, 'not_a_number', '1234567890']:
        cache = {'access_token': 'tok'}
        if bad is not None:
            cache['expires_at'] = bad
        c.token_file.write_text(json.dumps(cache), encoding='utf-8')
        assert c.check_access_token() is False, f'expires_at={bad!r} → False'
    print('✅ check_access_token: expires_at 부재/비정수 → False')


def test_check_access_token_returns_true_when_valid():
    """access_token + 유효한 expires_at만 있으면 True (app_key/app_secret 검증 안 함)."""
    c = _make_client_with_temp_token_file()
    c.token_file.write_text(json.dumps({
        'access_token': 'valid_token',
        'expires_at': 9999999999,  # 2286년까지 유효
        # app_key/app_secret 모두 캐시에 없음 (ARCH-011)
    }), encoding='utf-8')
    assert c.check_access_token() is True
    print('✅ check_access_token: 모든 조건 만족 (app_key 부재여도 OK) → True')


# ---------------------------------------------------------------------------- #
# issue_access_token — JSON 저장 + app_secret 제외                              #
# ---------------------------------------------------------------------------- #

def test_issue_access_token_writes_json_without_app_key_or_secret():
    """발급 후 토큰 캐시는 JSON이고 access_token + expires_at만 포함.

    ARCH-011: app_key/app_secret 모두 캐시에서 제외 (디스크 노출 최소화).
    """
    c = _make_client_with_temp_token_file(app_key='TEST_APP_KEY', app_secret='TOP_SECRET_VALUE')
    c.base_url = 'https://example.com'

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        'access_token': 'NEW_TOKEN',
        'access_token_token_expired': '2099-12-31 23:59:59',
    }
    with patch('src.kis.client.requests.post', return_value=fake_resp):
        c.issue_access_token()

    # 파일이 JSON으로 저장됨
    assert c.token_file.exists()
    raw = c.token_file.read_text(encoding='utf-8')
    parsed = json.loads(raw)  # JSON 파싱 가능 검증

    # 필수 키만 존재 (정확히 2개)
    assert parsed['access_token'] == 'NEW_TOKEN'
    assert isinstance(parsed['expires_at'], int)
    assert set(parsed.keys()) == {'access_token', 'expires_at'}, \
        f'캐시 키는 access_token + expires_at만 허용, 실제 {set(parsed.keys())}'

    # ★ app_key/app_secret 모두 캐시 부재 + 값이 파일에 노출 안 됨
    assert 'app_key' not in parsed, 'app_key가 캐시에 저장됨 (ARCH-011 위반)'
    assert 'app_secret' not in parsed, 'app_secret이 캐시에 저장됨 (ARCH-011 위반)'
    assert 'TEST_APP_KEY' not in raw, 'app_key 값이 파일에 노출되면 안 됨'
    assert 'TOP_SECRET_VALUE' not in raw, 'app_secret 값이 파일에 노출되면 안 됨'

    # access_token 인스턴스 변수에도 설정됨
    assert c.access_token == 'Bearer NEW_TOKEN'
    print('✅ issue_access_token: JSON 저장 + app_key/app_secret 모두 부재 (ARCH-011)')


def test_issue_access_token_chmod_token_file_to_0600():
    """토큰 파일 권한 0600 (소유자 RW만)로 제한."""
    c = _make_client_with_temp_token_file()
    c.base_url = 'https://example.com'

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        'access_token': 'TOK', 'access_token_token_expired': '2099-12-31 23:59:59',
    }
    with patch('src.kis.client.requests.post', return_value=fake_resp):
        c.issue_access_token()

    import stat
    mode = stat.S_IMODE(c.token_file.stat().st_mode)
    assert mode == 0o600, f'토큰 파일 권한 기대 0600, 실제 0o{mode:o}'
    print('✅ issue_access_token: 토큰 파일 권한 0600 (소유자 RW만)')


# ---------------------------------------------------------------------------- #
# load_access_token — JSON 로드                                                 #
# ---------------------------------------------------------------------------- #

def test_load_access_token_reads_json():
    """JSON 캐시에서 access_token을 'Bearer ' 접두로 self.access_token에 설정."""
    c = _make_client_with_temp_token_file()
    c.token_file.write_text(json.dumps({
        'access_token': 'LOADED_TOKEN',
        'expires_at': 9999999999,
        'app_key': 'TEST_APP_KEY',
    }), encoding='utf-8')

    c.load_access_token()
    assert c.access_token == 'Bearer LOADED_TOKEN'
    print('✅ load_access_token: JSON에서 access_token 정상 로드')


# ---------------------------------------------------------------------------- #
# 통합 — 손상 캐시 자동 폐기 → 재발급 흐름                                          #
# ---------------------------------------------------------------------------- #

def test_initialize_recovers_from_corrupted_cache():
    """initialize_access_token: 손상된 캐시(예: pickle)가 있어도 재발급 후 정상 로드."""
    c = _make_client_with_temp_token_file()
    c.base_url = 'https://example.com'
    # 손상 캐시 주입
    c.token_file.write_bytes(b'\x80\x04\x95garbage_pickle_bytes')

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        'access_token': 'RECOVERED_TOKEN',
        'access_token_token_expired': '2099-12-31 23:59:59',
    }
    with patch('src.kis.client.requests.post', return_value=fake_resp):
        c.initialize_access_token()

    assert c.access_token == 'Bearer RECOVERED_TOKEN', '손상 캐시 → 재발급 → load 흐름 통과'
    parsed = json.loads(c.token_file.read_text(encoding='utf-8'))
    assert parsed['access_token'] == 'RECOVERED_TOKEN'
    assert 'app_key' not in parsed
    assert 'app_secret' not in parsed
    print('✅ initialize_access_token: 손상 캐시 → 자동 재발급 → 정상 로드')


if __name__ == '__main__':
    test_check_access_token_returns_false_when_file_missing()
    test_check_access_token_returns_false_for_corrupted_json()
    test_check_access_token_returns_false_for_legacy_pickle_cache()
    test_check_access_token_returns_false_when_expired()
    test_check_access_token_returns_false_when_expires_at_missing_or_invalid()
    test_check_access_token_returns_true_when_valid()
    test_issue_access_token_writes_json_without_app_key_or_secret()
    test_issue_access_token_chmod_token_file_to_0600()
    test_load_access_token_reads_json()
    test_initialize_recovers_from_corrupted_cache()
    print('\n전체 테스트 통과')
