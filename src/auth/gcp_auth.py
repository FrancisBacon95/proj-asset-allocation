from google.oauth2.service_account import Credentials
import gspread
import pandas as pd
from src.config.env import GCP_KEY_PATH, EXECUTE_ENV
from google.auth import default

class GCPAuth():
    def __init__(self) -> None:
        self.scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive',
        ]

        # 로컬 환경에서는 JSON 키 파일을 사용하고, Cloud Run에서는 기본 자격 증명을 사용
        if EXECUTE_ENV == 'LOCAL':
            # 키 파일 경로가 제공되면 키 파일을 사용한 인증
            self.credential = Credentials.from_service_account_file(GCP_KEY_PATH, scopes=self.scope)
        else:
            #  키 파일 없이 기본 자격 증명 (ADC)을 사용
            self.credential, _ = default(scopes=self.scope)
        
        # gspread를 사용하여 Google Sheets API 인증
        self.client = gspread.authorize(self.credential)

    def get_df_from_google_sheets(self, url, sheet) -> pd.DataFrame:
        #개인에 따라 수정 필요 - 스프레드시트 url 가져오기
        doc = self.client.open_by_url(url)
        sheet = doc.worksheet(sheet)
        df = pd.DataFrame(sheet.get_all_values())
        return df.rename(columns=df.iloc[0]).drop(df.index[0])
