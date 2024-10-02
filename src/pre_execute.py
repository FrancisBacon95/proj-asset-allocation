from datetime import date
import requests
import pandas as pd
import xmltodict

from src.auth import GCPAuth
from src.config.env import GOOGLE_SHEET_URL, PUBLIC_DATA_API_KEY
from src.config.helper import log_method_call

@log_method_call
def check_holiday(target_date: date) -> bool:
    url = 'http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getHoliDeInfo'
    params ={
        'serviceKey' : PUBLIC_DATA_API_KEY, 
        'pageNo' : '1', 
        'numOfRows' : '10', 
        'solYear' : str(target_date.year), 
        'solMonth' : str(target_date.month).zfill(2),
    }

    response = requests.get(url, params=params, timeout=90)
    data = xmltodict.parse(response.content)['response']['body']['items']['item']
    data = pd.DataFrame(data)
    
    kst_date_str = target_date.strftime('%Y%m%d')
    return True if data.loc[(data['isHoliday'] == 'Y') & (data['locdate'] == kst_date_str)].shape[0] > 0 else False

log_method_call
def check_already_executed(target_date: date, account: str) -> bool:
    latest_trade_date: date = pd.to_datetime(GCPAuth(url=GOOGLE_SHEET_URL).get_df_from_google_sheets(f'{account}_trade_log')['update_dt']).dt.date.unique()[0]
    return True if (target_date - latest_trade_date).days < 7 else False

@log_method_call
def is_executable(target_date: date, account: str):
    is_holiday = check_holiday(target_date=target_date)
    is_already_executed = check_already_executed(target_date=target_date, account=account)
    print(f'- is_holiday: {is_holiday}')
    print(f'- is_already_executed: {is_already_executed}')
    if is_holiday or is_already_executed:
        return False
    return True