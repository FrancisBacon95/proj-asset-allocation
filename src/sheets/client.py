import pandas as pd
import gspread
from google.auth import default
from google.oauth2.service_account import Credentials
from src.config.env import GCP_KEY_PATH, EXECUTE_ENV

class GoogleSheetsClient():
    def __init__(self, url) -> None:
        self.gs_url = url
        # 읽기·쓰기 모두 필요 (overwrite_dataframe). readonly 스코프로는 add_worksheet/clear/update 불가.
        self.scope = [
            'https://www.googleapis.com/auth/spreadsheets',
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

    def get_worksheet_url(self, sheet: str) -> str:
        """특정 시트 탭의 URL을 반환한다. 탭이 없으면 스프레드시트 기본 URL을 반환한다."""
        try:
            ws = self.get_worksheet(sheet)
            return f'{self.spreadsheet.url}#gid={ws.id}'
        except gspread.exceptions.WorksheetNotFound:
            return self.spreadsheet.url

    def overwrite_dataframe(self, sheet_name: str, df: pd.DataFrame) -> gspread.Worksheet:
        """지정 탭에 DataFrame을 헤더+데이터로 덮어쓴다.

        - 탭이 없으면 생성, 있으면 clear() 후 update().
        - DataFrame의 NaN/None은 빈 문자열로, 모든 값은 str로 변환해 기록.
        - 빈 DataFrame이어도 헤더 행은 기록한다 (스키마 보존).

        Returns:
            gspread.Worksheet: 작성된 워크시트 객체.
        """
        try:
            ws = self.spreadsheet.worksheet(sheet_name)
            ws.clear()
        except gspread.exceptions.WorksheetNotFound:
            # add_worksheet는 rows/cols 필수. DataFrame 크기 + 여유분으로 확보.
            rows = max(len(df) + 10, 100)
            cols = max(len(df.columns), 10)
            ws = self.spreadsheet.add_worksheet(title=sheet_name, rows=rows, cols=cols)

        header = [list(df.columns.astype(str))]
        if df.empty:
            ws.update(range_name='A1', values=header)
            return ws

        body = df.fillna('').astype(str).values.tolist()
        ws.update(range_name='A1', values=header + body)
        return ws
