import os
import json
from pathlib import Path
from dataclasses import dataclass

EXECUTE_ENV = os.getenv("EXECUTE_ENV")
GCP_KEY_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", '')
KIS_KEY_PATH = os.getenv("KIS_API_AUTH_PATH")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class KISAuthConfig:
    account_type: str
    user_id: str
    account_number: str
    app_key: str
    app_secret: str


def load_kis_auth_config(account_type: str) -> KISAuthConfig:
    if not KIS_KEY_PATH:
        raise EnvironmentError("KIS_API_AUTH_PATH 환경 변수가 설정되지 않았습니다.")
    path = Path(KIS_KEY_PATH)
    if not path.exists():
        raise FileNotFoundError(f"KIS 인증 파일을 찾을 수 없습니다: {path}")
    with path.open('r') as f:
        kis_auth_data = json.load(f)
    account_data = kis_auth_data.get(account_type)
    if account_data is None:
        available = list(kis_auth_data.keys())
        raise KeyError(f"account_type '{account_type}'이 KIS 인증 파일에 없습니다. 가능한 값: {available}")
    required_keys = ("USER_ID", "ACCOUNT_NUMBER", "APP_KEY", "APP_SECRET")
    missing = [k for k in required_keys if not account_data.get(k)]
    if missing:
        raise KeyError(f"KIS 인증 파일 '{account_type}'에 필수 키 누락: {missing}")
    return KISAuthConfig(
        account_type=account_type,
        user_id=account_data["USER_ID"],
        account_number=account_data["ACCOUNT_NUMBER"],
        app_key=account_data["APP_KEY"],
        app_secret=account_data["APP_SECRET"],
    )
