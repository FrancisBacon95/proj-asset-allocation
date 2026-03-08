# proj-asset-allocation

정적 자산 배분 전략을 자동으로 실행하는 리밸런싱 자동화 도구입니다.
한국투자증권 Open API와 Google Sheets를 기반으로 동작합니다.

## 동작 방식

![사용 흐름](./simple_use_case_diagram.jpg)

1. Google Sheets에서 목표 자산 배분 비중을 읽어옵니다.
2. 한국투자증권 API로 현재 잔고 및 시세를 조회합니다.
3. 목표 비중과 현재 잔고를 비교해 매수/매도 계획을 수립합니다.
4. 계획에 따라 주문을 실행하고, 결과를 Google Sheets에 기록합니다.

거래일 여부 및 최근 7일 내 실행 여부를 확인하여 중복 실행을 방지합니다.

## 실행 방법

```bash
python main.py --account_type <계좌타입>
```

`account_type`은 `kis_api_auth.json`에 정의된 계좌 키 값과 일치해야 합니다.

## 환경 설정

프로젝트 루트에 `.env` 파일을 생성합니다.

```env
EXECUTE_ENV=LOCAL
KIS_API_AUTH_PATH=/path/to/kis_api_auth.json
GOOGLE_SERVICE_ACCOUNT_PATH=/path/to/gcp_service_account.json
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/...
```

| 변수 | 설명 |
|---|---|
| `EXECUTE_ENV` | 실행 환경 (`LOCAL` 또는 Cloud Run) |
| `KIS_API_AUTH_PATH` | 한국투자증권 API 인증 정보 JSON 경로 |
| `GOOGLE_SERVICE_ACCOUNT_PATH` | GCP 서비스 계정 JSON 경로 (LOCAL 환경에서만 필요) |
| `GOOGLE_SHEET_URL` | 자산 배분 설정 및 거래 로그를 저장할 Google Sheets URL |

### KIS API 인증 파일 형식 (`kis_api_auth.json`)

```json
{
  "<account_type>": {
    "USER_ID": "...",
    "ACCOUNT_NUMBER": "XXXXXXXX-XX",
    "APP_KEY": "...",
    "APP_SECRET": "..."
  }
}
```

### Google Sheets 구성

| 시트명 | 용도 |
|---|---|
| `{account_type}_allocation` | 목표 자산 배분 비중 (`ticker`, `weight` 컬럼) |
| `{account_type}_trade_log` | 리밸런싱 실행 결과 로그 |

## 프로젝트 구조

```
src/
├── logger.py           # 로깅 설정 및 log_method_call 데코레이터
├── allocation.py       # 리밸런싱 핵심 로직 (StaticAllocationAgent)
├── config/
│   └── env.py          # 환경변수 로딩 및 KIS 인증 설정
├── kis/
│   ├── client.py       # 한국투자증권 REST API 클라이언트 (KISClient)
│   └── stock_config.py # 거래소 코드 및 통화 상수
└── sheets/
    └── client.py       # Google Sheets 읽기/쓰기 클라이언트 (GoogleSheetsClient)
```

## 의존성

Python 3.10 이상, [uv](https://github.com/astral-sh/uv) 기반으로 의존성을 관리합니다.

```bash
uv sync
```

주요 패키지:

| 패키지 | 용도 |
|---|---|
| `gspread` | Google Sheets API |
| `pandas` | 데이터 처리 |
| `yfinance` | 환율 조회 (해외주식 원화 환산) |
| `python-dotenv` | `.env` 파일 로딩 |

## 배포

Google Cloud Run 환경을 지원합니다. `EXECUTE_ENV`를 `LOCAL` 이외의 값으로 설정하면 GCP Application Default Credentials(ADC)를 사용합니다.

```bash
gcloud builds submit --config cloudbuild.yaml
```
