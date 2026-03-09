from datetime import datetime
import pytz
import pandas as pd
import gspread
from google.auth import default
from google.oauth2.service_account import Credentials
from src.config.env import GCP_KEY_PATH, EXECUTE_ENV

class GoogleSheetsClient():
    def __init__(self, url) -> None:
        self.gs_url = url
        self.scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive',
        ]
        # 로컬 환경에서는 JSON 키 파일을 사용하고, Cloud Run에서는 기본 자격 증명을 사용
        if EXECUTE_ENV == 'LOCAL':
            self.credential = Credentials.from_service_account_file(GCP_KEY_PATH, scopes=self.scope)
        else:
            self.credential, _ = default(scopes=self.scope)

        self.client = gspread.authorize(self.credential)
        self.spreadsheet = self.client.open_by_url(self.gs_url)

    def get_worksheet(self, sheet) -> gspread.Worksheet:
        return self.spreadsheet.worksheet(sheet)

    def get_df_from_google_sheets(self, sheet) -> pd.DataFrame:
        worksheet = self.get_worksheet(sheet)
        df = pd.DataFrame(worksheet.get_all_values())
        return df.rename(columns=df.iloc[0]).drop(df.index[0])

    def write_worksheet(self, df: pd.DataFrame, worksheet_name: str) -> None:
        tmp = df.copy()
        tmp['update_dt'] = datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
        tmp[tmp.select_dtypes(include=['float']).columns] = tmp.select_dtypes(include=['float']).round(2)
        tmp[tmp.select_dtypes(include=['float', 'int']).columns] = tmp[tmp.select_dtypes(include=['float', 'int']).columns].fillna(0)
        tmp = tmp.fillna('')
        real_worksheet_list = list(map(lambda x: x.title, self.spreadsheet.worksheets()))
        if worksheet_name not in real_worksheet_list:
            self.spreadsheet.add_worksheet(title=worksheet_name, rows=tmp.shape[0]+10, cols=tmp.shape[1]+5)
        worksheet = self.spreadsheet.worksheet(worksheet_name)
        worksheet.update([tmp.columns.values.tolist()] + tmp.values.tolist())
