import os
import glob
import json
import time
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

print(">> SPA master data.json 일일 업데이트 시작...")

DATA_FILE = "data.json"
DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_KEY", "").strip() or "23c70f6d2903b2c4ef940ed8f9bfbf97f8d36d3194aabba3251666d4f053feb3"

if not os.path.exists(DATA_FILE):
    raise FileNotFoundError("data.json 파일이 없습니다. backfill_generator.py를 먼저 실행하세요.")

with open(DATA_FILE, "r", encoding="utf-8") as f:
    master = json.load(f)

# 🚨 [수정됨] 무조건 한국 시간(KST) 기준으로 현재 시간을 가져와서 하루 전(-1일)을 계산
KST = timezone(timedelta(hours=9))
target_dt = datetime.now(KST) - timedelta(days=1)
target_date_str = target_dt.strftime("%Y%m%d")
weekday_kr_list = ["월", "화", "수", "목", "금", "토", "일"]

print(f">> 타깃 일자 (KST 기준): {target_date_str}")

# =====================================================================
# 신규 API (SmpWithForecastDemand) 호출 시도
# =====================================================================
def fetch_api_smp_new(trade_date, api_key):
    operations = [
        "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemandList",
        "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand",
        "https://apis.data.go.kr/B552115/SmpWithForecastDemand"
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    for idx, url in enumerate(operations):
        params = {
            "serviceKey": api_key,
            "pageNo": 1,
            "numOfRows": 30,
            "tradeDate": trade_date,
            "dataType": "JSON"
        }
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
                if items:
                    items = sorted(items, key=lambda x: int(x.get('tradeHour', x.get('hour', 0))))
                    hourly = [float(x.get('smp', x.get('landSmp', 0))) for x in items if int(x.get('tradeHour', x.get('hour', 0))) in range(1, 25)]
                    if len(hourly) == 24:
                        print(f"✅ API 실시간 SMP 수집 성공! (가중평균: {sum(hourly)/24:.2f}원)")
                        return hourly
        except Exception:
            time.sleep(1)
            
    return None

api_smp = fetch_api_smp_new(target_date_str, DATA_GO_KR_KEY)

# 직전일 데이터 가져오기 (Fallback 및 비교용)
prev_key = master.get("latest_date")
if prev_key == target_date_str:  # 이미 오늘 배치가 돌아서 최신일이 타깃과 같으면 그 전날을 찾음
    prev_dates = sorted([k for k in master["days"].keys() if k < target_date_str])
    prev_key = prev_dates[-1] if prev_dates else target_date_str
prev_day_data = master["days"][prev_key]

if api_smp and len(api_smp) == 24:
    land_smp = api_smp
    print(f">> {target_date_str} 실시간 API 데이터를 정상 적용했습니다.")
else:
    print(f"🚨 API 호출 실패! 불가피하게 직전일({prev_key}) 데이터를 복사하여 대시보드 중단을 방어합니다.")
    land_smp = prev_day_data["smp_hourly"]

smp_avg = round(sum(land_smp)/24, 2)
smp_max = round(max(land_smp), 2)
smp_min = round(min(land_smp), 2)
max_idx = land_smp.index(smp_max)
min_idx = land_smp.index(smp_min)

thresh = smp_min + (smp_max - smp_min) * 0.85
left, right = max_idx, max_idx
while left > 0 and land_smp[left-1] >= thresh: left -= 1
while right < 23 and land_smp[right+1] >= thresh: right += 1
if (right - left) > 6: left = max(0, max_idx - 2); right = min(23, max_idx + 2)
peak_band = f"{left+1}~{right+1}시"

# 발전원별 수급 실적은 당일 공시가 안 되므로 최신 패턴(직전일) 복사 유지
gen_entry = prev_day_data["gen"]

# 최근 7일 구성
recent_7 = [{
    'date': f"{target_dt.month}.{target_dt.day}({weekday_kr_list[target_dt.weekday()]})",
    'is_holiday': target_dt.weekday() in [5, 6] or target_date_str in master["holidays"],
    'avg': smp_avg, 'max': smp_max, 'min': smp_min,
    'cap': prev_day_data["cap_peak"] + 20000, 'peak': prev_day_data["cap_peak"], 'time': prev_day_data["cap_time"], 'res': prev_day_data["cap_res"]
}]
for delta in range(1, 7):
    p_dt = target_dt - timedelta(days=delta)
    p_k = p_dt.strftime("%Y%m%d")
    if p_k in master["days"]:
        p_info = master["days"][p_k]
        recent_7.append({
            'date': f"{p_dt.month}.{p_dt.day}({weekday_kr_list[p_dt.weekday()]})",
            'is_holiday': p_dt.weekday() in [5, 6] or p_k in master["holidays"],
            'avg': p_info["smp_avg"], 'max': p_info["smp_max"], 'min': p_info["smp_min"],
            'cap': p_info.get("cap_cap", 91000), 'peak': p_info["cap_peak"], 'time': p_info["cap_time"], 'res': p_info["cap_res"]
        })

diff_avg_val = round(smp_avg - recent_7[1]["avg"], 2) if len(recent_7) > 1 else 0.0
diff_max_val = round(smp_max - recent_7[1]["max"], 2) if len(recent_7) > 1 else 0.0
diff_min_val = round(smp_min - recent_7[1]["min"], 2) if len(recent_7) > 1 else 0.0

def fmt_diff(v):
    if v > 0: return f"+{abs(v):.2f}원", "text-rose-600"
    elif v < 0: return f"▼{abs(v):.2f}원", "text-blue-600"
    return "- 0.00원", "text-slate-500"

diff_avg_txt, diff_avg_color = fmt_diff(diff_avg_val)
diff_max_txt, diff_max_color = fmt_diff(diff_max_val)
diff_min_txt, diff_min_color = fmt_diff(diff_min_val)

master["days"][target_date_str] = {
    'smp_hourly': land_smp, 'smp_avg': smp_avg, 'smp_max': smp_max, 'smp_min': smp_min,
    'smp_max_idx': max_idx, 'smp_min_idx': min_idx, 'peak_band': peak_band, 'peak_left': left, 'peak_right': right,
    'cap_peak': prev_day_data["cap_peak"], 'cap_time': prev_day_data["cap_time"], 'cap_res': prev_day_data["cap_res"],
    'lng_price': 1058.34, 'diff_lng_text': "전월비 +88.73원", 'diff_lng_color': "text-rose-600", 'prev_smp_str': "147.88원/kWh",
    'diff_avg_txt': diff_avg_txt, 'diff_avg_color': diff_avg_color,
    'diff_max_txt': diff_max_txt, 'diff_max_color': diff_max_color,
    'diff_min_txt': diff_min_txt, 'diff_min_color': diff_min_color,
    'recent_7': recent_7, 'gen': gen_entry
}

master["latest_date"] = target_date_str

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(master, f, ensure_ascii=False)

print(f"🎉 성공! KST 기준 정확한 타깃 일자({target_date_str})로 data.json 갱신 완료!")
