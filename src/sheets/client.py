"""Google Sheets 클라이언트.

`{account_type}_allocation` 시트에서 목표 비중을 읽고, IRP 계좌 실행 시
`{account_type}_action_plan` 시트를 덮어쓴다.

핵심 설계:
- OAuth scope: `spreadsheets` (read+write). IRP action plan 쓰기 때문에
  readonly로는 동작 안 함.
- `overwrite_dataframe` 트랜잭션 안전: clear→update가 아니라 update→trailing
  batch_clear 순서. update 실패 시 batch_clear 미호출 → 기존 시트 데이터 보존.
- NaN/None은 빈 문자열로 변환해 'NaN' 문자열이 시트에 박히는 것을 방지.
- 빈 DataFrame이어도 헤더는 기록 (스키마 보존).
- 신규 시트는 add_worksheet로 자동 생성 (rows≥100, cols≥10 여유분 확보).
"""
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
        """지정 탭에 DataFrame을 헤더+데이터로 덮어쓴다 (트랜잭션 안전 구조).

        흐름:
        1) 탭이 없으면 add_worksheet로 생성 (이때만 새 시트가 빈 상태).
        2) update를 먼저 시도. 성공해야만 다음 단계로 진행.
        3) 새 데이터 길이를 초과하는 트레일링 행만 batch_clear로 정리.

        => update 실패 시 기존 시트 내용은 그대로 유지된다 (clear→update 순서가
        update 실패 시 시트를 빈 상태로 남기는 위험을 회피).

        - DataFrame의 NaN/None은 빈 문자열로, 모든 값은 str로 변환해 기록.
        - 빈 DataFrame이어도 헤더 행은 기록한다 (스키마 보존).

        Returns:
            gspread.Worksheet: 작성된 워크시트 객체.
        """
        try:
            ws = self.spreadsheet.worksheet(sheet_name)
            is_new_sheet = False
        except gspread.exceptions.WorksheetNotFound:
            # add_worksheet는 rows/cols 필수. DataFrame 크기 + 여유분으로 확보.
            rows = max(len(df) + 10, 100)
            cols = max(len(df.columns), 10)
            ws = self.spreadsheet.add_worksheet(title=sheet_name, rows=rows, cols=cols)
            is_new_sheet = True

        header = [list(df.columns.astype(str))]
        if df.empty:
            rows_to_write = header
        else:
            body = df.fillna('').astype(str).values.tolist()
            rows_to_write = header + body

        # 새 데이터를 먼저 update — 실패 시 기존 시트 내용 보존됨
        ws.update(range_name='A1', values=rows_to_write)

        # 트레일링 행 정리: 기존 시트인 경우만, 새 데이터 길이 이후 잔존 행 batch_clear
        if not is_new_sheet:
            new_row_count = len(rows_to_write)
            last_row = ws.row_count
            if last_row > new_row_count:
                ws.batch_clear([f'A{new_row_count + 1}:{last_row}'])
        return ws
