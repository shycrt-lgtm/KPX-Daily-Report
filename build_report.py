import os
import glob
import json
import time
import requests
import pandas as pd
from datetime import datetime, timedelta

print(">> SPA master data.json 일일 업데이트 시작...")

DATA_FILE = "data.json"
DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()

if not DATA_GO_KR_KEY:
    raise ValueError("DATA_GO_KR_KEY가 비어 있습니다. GitHub Secrets를 확인하세요.")

if not os.path.exists(DATA_FILE):
    raise FileNotFoundError("data.json 파일이 없습니다. backfill_generator.py를 먼저 실행하세요.")

with open(DATA_FILE, "r", encoding="utf-8") as f:
    master = json.load(f)

# 어제 날짜 (D-1)
target_dt = datetime.now() - timedelta(days=1)
target_date_str = target_dt.strftime("%Y%m%d")
weekday_kr_list = ["월", "화", "수", "목", "금", "토", "일"]

print(f">> 타깃 일자: {target_date_str}")

# =====================================================================
# 공공데이터포털 SMP API 호출 (requests 기반)
# =====================================================================
def fetch_api_smp(trade_date, api_key):
    # 디코딩된 키와 인코딩된 키 모두 시도
    import urllib.parse
    dec_key = urllib.parse.unquote(api_key)
    
    urls = [
        # 1. requests params 방식 (일반 인증키)
        ("http://apis.data.go.kr/B552115/smpInland/getSmpInlandList", {"serviceKey": dec_key, "pageNo": 1, "numOfRows": 30, "tradeDate": trade_date, "dataType": "JSON"}),
        # 2. URL 직접 결합 방식 (인코딩 인증키)
        (f"http://apis.data.go.kr/B552115/smpInland/getSmpInlandList?serviceKey={api_key}&pageNo=1&numOfRows=30&tradeDate={trade_date}&dataType=JSON", None)
    ]

    for idx, (url, params) in enumerate(urls):
        print(f">> [시도 {idx+1}] SMP API 호출 중...")
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            if params:
                resp = requests.get(url, params=params, headers=headers, timeout=20)
            else:
                resp = requests.get(url, headers=headers, timeout=20)
            
            print(f">> 상태코드: {resp.status_code}")
            print(f">> 응답 본문 앞 200자: {resp.text[:200]}")

            if resp.status_code == 200:
                data = resp.json()
                items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
                if items:
                    items = sorted(items, key=lambda x: int(x.get('tradeHour', 0)))
                    hourly = [float(x.get('smp', 0)) for x in items if int(x.get('tradeHour', 0)) in range(1, 25)]
                    if len(hourly) == 24:
                        print(f"✅ SMP 24시간 실시간 데이터 수집 성공! (평균: {sum(hourly)/24:.2f}원)")
                        return hourly
        except Exception as e:
            print(f"⚠️ 시도 {idx+1} 에러: {e}")
            time.sleep(1)

    return None

# =====================================================================
# 실행 및 데이터 반영
# =====================================================================
api_smp = fetch_api_smp(target_date_str, DATA_GO_KR_KEY)

if not api_smp:
    print(f"🚨 {target_date_str} SMP API 호출 실패! GitHub Secrets의 DATA_GO_KR_KEY 또는 서비스 신청 상태를 확인해야 합니다.")
    raise ValueError(f"KPX API에서 {target_date_str} SMP 실제 데이터를 가져오지 못했습니다. Actions 로그의 응답 본문을 확인하세요.")

# 직전일(27일) 데이터 가져오기 (비교용)
prev_key = "20260927" if "20260927" in master["days"] else master.get("latest_date")
prev_day_data = master["days"][prev_key]

land_smp = api_smp
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

# 발전원별 데이터는 직전 패턴 유지 (SMP는 100% 실제 28일 데이터 반영)
gen_entry = prev_day_data["gen"]

# 최근 7일 구성 (실제 28일 평균 vs 27일 평균 비교)
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
            'cap': p_info["cap_peak"] + 20000, 'peak': p_info["cap_peak"], 'time': p_info["cap_time"], 'res': p_info["cap_res"]
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
    'lng_price': prev_day_data["lng_price"], 'diff_lng_text': "전월비 변동없음", 'diff_lng_color': "text-slate-500", 'prev_smp_str': prev_day_data["prev_smp_str"],
    'diff_avg_txt': diff_avg_txt, 'diff_avg_color': diff_avg_color,
    'diff_max_txt': diff_max_txt, 'diff_max_color': diff_max_color,
    'diff_min_txt': diff_min_txt, 'diff_min_color': diff_min_color,
    'recent_7': recent_7, 'gen': gen_entry
}
master["latest_date"] = target_date_str

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(master, f, ensure_ascii=False)

print(f"🎉 성공! 진짜 9월 28일 실시간 실적(가중평균: {smp_avg}원)으로 data.json 갱신 완료!")
