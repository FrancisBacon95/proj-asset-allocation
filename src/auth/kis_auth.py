'''
한국투자증권 python wrapper
'''
import requests
import json
from zoneinfo import ZoneInfo
from datetime import datetime
import pickle

from src.config.env import PROJECT_ROOT, load_kis_auth_config
class KISAuth():
    def __init__(self, account_type: str) -> None:
        """생성자
        Args:
            app_key (str): 발급받은 app key
            app_secret (str): 발급받은 app secret
            acc_no (str): 계좌번호 체계의 앞 8자리-뒤 2자리
        """
        self.auth_config = load_kis_auth_config(account_type)
        self.acc_no = self.auth_config.account_number
        self.acc_no_prefix = self.acc_no.split('-')[0]
        self.acc_no_postfix = self.acc_no.split('-')[1]

        self.mock = False
        self.set_base_url(self.mock)

        self.token_file = PROJECT_ROOT / f'kis_token_{account_type}.dat'
        self.access_token = None
        
        # 토큰 설정 및 발급
        self.initialize_access_token()

    def set_base_url(self, mock: bool = True) -> None:
        """테스트(모의투자) 서버 사용 설정
        Args:
            mock(bool, optional): True: 테스트서버, False: 실서버 Defaults to True.
        """
        self.base_url = (
            "https://openapivts.koreainvestment.com:29443" if mock
            else "https://openapi.koreainvestment.com:9443"
        )

    def initialize_access_token(self) -> None:
        """토큰을 초기화하고 발급합니다."""
        if not self.check_access_token():
            self.issue_access_token()
        self.load_access_token()


    def check_access_token(self) -> bool:
        """check access token

        Returns:
            Bool: True: token is valid, False: token is not valid
        """
        
        # 존재 여부 확인
        if not self.token_file.exists():
            return False
        
        with self.token_file.open("rb") as f:
            data = pickle.load(f)

        # 존재하는 토큰이 현재 API KEY/SECRET 정보와 일치하는지 확인
        if (data['app_key'] != self.auth_config.app_key) or (data['app_secret'] != self.auth_config.app_secret):
            return False

        # 토큰 만료기간 이전인지 확인
        good_until = data['timestamp']
        current_timestamp = int(datetime.now().timestamp())
        return current_timestamp < good_until
    
    def issue_access_token(self) -> None:
        """OAuth인증/접근토큰발급
        """
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

        # 'expires_in' has no reference time and causes trouble:
        # The server thinks I'm expired but my token.dat looks still valid!
        # Hence, we use 'access_token_token_expired' here.
        # This error is quite big. I've seen 4000 seconds.
        timezone = ZoneInfo('Asia/Seoul')
        dt = datetime.strptime(resp_data['access_token_token_expired'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone)
        resp_data['timestamp'] = int(dt.timestamp())
        resp_data['app_key'] = self.auth_config.app_key
        resp_data['app_secret'] = self.auth_config.app_secret

        # dump access token
        with self.token_file.open("wb") as f:
            pickle.dump(resp_data, f)

    def load_access_token(self):
        """load access token
        """
        with self.token_file.open("rb") as f:
            data = pickle.load(f)
        self.access_token = f'Bearer {data["access_token"]}'

    def issue_hashkey(self, data: dict):
        """해쉬키 발급
        Args:
            data (dict): POST 요청 데이터
        Returns:
            _type_: _description_
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
        haskkey = resp.json()["HASH"]
        return haskkey