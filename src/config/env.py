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
    with open(KIS_KEY_PATH, 'r') as file:
        kis_auth_data = json.load(file)
    account_data = kis_auth_data.get(account_type)

    return KISAuthConfig(
        account_type=account_type,
        user_id=account_data["USER_ID"],
        account_number=account_data["ACCOUNT_NUMBER"],
        app_key=account_data["APP_KEY"],
        app_secret=account_data["APP_SECRET"]
    ) 
