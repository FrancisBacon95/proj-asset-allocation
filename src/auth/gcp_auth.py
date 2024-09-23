from datetime import datetime
import pytz
import pandas as pd
import gspread
from google.auth import default
from google.oauth2.service_account import Credentials
from src.config.env import GCP_KEY_PATH, EXECUTE_ENV

class GCPAuth():
    def __init__(self, url) -> None:
        self.gs_url = url
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
        self.spreadsheet = self.client.open_by_url(self.gs_url)

    def get_worksheet(self, sheet) -> gspread.worksheet:
        return self.spreadsheet.worksheet(sheet)

    def get_df_from_google_sheets(self, sheet) -> pd.DataFrame:
        #개인에 따라 수정 필요 - 스프레드시트 url 가져오기
        worksheet = self.get_worksheet(sheet)
        df = pd.DataFrame(worksheet.get_all_values())
        return df.rename(columns=df.iloc[0]).drop(df.index[0])
    
    def write_worksheet(self, df: pd.DataFrame, worksheet_name: str) -> None:
        tmp = df.copy()
        tmp['update_dt'] = datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
        real_worksheet_list = list(map(lambda x: x.title, self.spreadsheet.worksheets()))
        if worksheet_name not in real_worksheet_list:
            self.spreadsheet.add_worksheet(title=worksheet_name, rows=tmp.shape[0]+10, cols=tmp.shape[1]+5)
        worksheet = self.spreadsheet.worksheet(worksheet_name)
        worksheet.update([tmp.columns.values.tolist()] + tmp.values.tolist())
