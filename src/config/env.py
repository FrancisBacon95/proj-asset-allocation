from pathlib import Path
from dataclasses import dataclass
import os
import json

GCP_KEY_FILE = "gcp_service_account.json"
KIS_KEY_FILE = "kis_api_auth.json"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent 

GCP_KEY_PATH = PROJECT_ROOT / GCP_KEY_FILE
KIS_KEY_PATH = PROJECT_ROOT / KIS_KEY_FILE

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

    # KISAuthConfig 데이터 클래스로 변환
    return KISAuthConfig(
        account_type=account_type,
        user_id=account_data["USER_ID"],
        account_number=account_data["ACCOUNT_NUMBER"],
        app_key=account_data["APP_KEY"],
        app_secret=account_data["APP_SECRET"]
    ) 
