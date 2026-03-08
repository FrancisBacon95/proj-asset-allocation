import sys
from pathlib import Path
current_dir = Path(__file__).resolve()
project_root = current_dir.parent.parent
sys.path.append(str(project_root))

import json
from unittest.mock import patch
from dotenv import load_dotenv

load_dotenv(project_root / '.env')

# KISAgent.__init__의 환율 조회 우회 (fetch_holiday와 무관)
with patch('yfinance.Ticker.history', return_value=__import__('pandas').DataFrame({'Close': [1450.0]})):
    from src.core.kis_agent import KISAgent
    agent = KISAgent('COMMON')

# 테스트: 오늘 기준 휴장일 조회
base_dt = '20260308'
result = agent.fetch_holiday(base_dt)

print(f"=== fetch_holiday('{base_dt}') ===")
print(f"총 {len(result)}건\n")
print(json.dumps(result[:5], ensure_ascii=False, indent=2))

# 개장일만 필터링
open_days = [r for r in result if r['opnd_yn'] == 'Y']
print(f"\n개장일: {len(open_days)}건")
for d in open_days[:5]:
    print(f"  {d['bass_dt']} (요일코드: {d['wday_dvsn_cd']}) - 개장:{d['opnd_yn']} / 영업:{d['bzdy_yn']}")

# 휴장일만 필터링
closed_days = [r for r in result if r['opnd_yn'] == 'N']
print(f"\n휴장일: {len(closed_days)}건")
for d in closed_days[:5]:
    print(f"  {d['bass_dt']} (요일코드: {d['wday_dvsn_cd']}) - 개장:{d['opnd_yn']} / 영업:{d['bzdy_yn']}")
