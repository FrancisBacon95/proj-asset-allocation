from oauth2client.service_account import ServiceAccountCredentials
import gspread
import pandas as pd
from src.config.env import GCP_KEY_PATH

class GCPAuth():
    def __init__(self) -> None:
        self.scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive',
        ]
        #개인에 따라 수정 필요 - 다운로드 받았던 키 값 경로 
        self.credential = ServiceAccountCredentials.from_json_keyfile_name(GCP_KEY_PATH, self.scope)
        self.client = gspread.authorize(self.credential)

    def get_df_from_google_sheets(self, url, sheet) -> pd.DataFrame:
        #개인에 따라 수정 필요 - 스프레드시트 url 가져오기
        doc = self.client.open_by_url(url)
        sheet = doc.worksheet(sheet)
        df = pd.DataFrame(sheet.get_all_values())
        return df.rename(columns=df.iloc[0]).drop(df.index[0])
