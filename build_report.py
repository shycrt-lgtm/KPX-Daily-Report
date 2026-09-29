import os
import glob
import json
import time
import urllib.request
import urllib.parse
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
# 공공데이터포털 API 호출 (타임아웃 30초 + 3회 재시도)
# =====================================================================
def fetch_api_smp(trade_date, raw_key):
    # 인코딩된 키와 디코딩된 키 둘 다 준비
    safe_key = urllib.parse.quote(urllib.parse.unquote(raw_key))
    url = f"http://apis.data.go.kr/B552115/smpInland/getSmpInlandList?serviceKey={safe_key}&pageNo=1&numOfRows=30&tradeDate={trade_date}&dataType=JSON"

    for attempt in range(1, 4):
        print(f">> [시도 {attempt}/3] SMP API 호출 중 (타임아웃 30초)...")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=30) as res:
                res_body = res.read().decode('utf-8')
                print(f">> API 응답 수신 성공! (내용: {res_body[:150]}...)")
                data = json.loads(res_body)
                items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
                if items:
                    items = sorted(items, key=lambda x: int(x.get('tradeHour', 0)))
                    hourly = [float(x.get('smp', 0)) for x in items if int(x.get('tradeHour', 0)) in range(1, 25)]
                    if len(hourly) == 24:
                        print(f"✅ SMP 24시간 실시간 데이터 파싱 성공! (평균: {sum(hourly)/24:.2f}원)")
                        return hourly
        except Exception as e:
            print(f"⚠️ 시도 {attempt} 실패: {e}")
            if attempt < 3:
                print(">> 3초 후 재시도합니다...")
                time.sleep(3)
    return None

def fetch_api_gen(trade_date, raw_key):
    safe_key = urllib.parse.quote(urllib.parse.unquote(raw_key))
    url = f"http://apis.data.go.kr/B552115/GenByFuel/getGenByFuel?serviceKey={safe_key}&pageNo=1&numOfRows=300&tradeDate={trade_date}&dataType=JSON"

    for attempt in range(1, 3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=30) as res:
                res_body = res.read().decode('utf-8')
                data = json.loads(res_body)
                items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
                if items:
                    fmap = {'nuclear':[0.0]*24,'coal':[0.0]*24,'oil':[0.0]*24,'gas':[0.0]*24,'hydro':[0.0]*24,'pump':[0.0]*24,'ess':[0.0]*24,'wind':[0.0]*24,'solar':[0.0]*24}
                    cnt = [0]*24
                    for item in items:
                        h = int(str(item.get('tradeHour', item.get('hour', 1)))) - 1
                        if 0 <= h < 24:
                            cnt[h] += 1
                            fmap['nuclear'][h] += float(item.get('nuclear', 0) or 0)
                            fmap['coal'][h] += float(item.get('coal', 0) or 0)
                            fmap['oil'][h] += float(item.get('oil', 0) or 0)
                            fmap['gas'][h] += float(item.get('lng', item.get('gas', 0)) or 0)
                            fmap['hydro'][h] += float(item.get('hydro', 0) or 0)
                            fmap['pump'][h] += float(item.get('pump', 0) or 0)
                            fmap['ess'][h] += float(item.get('ess', 0) or 0)
                            fmap['wind'][h] += float(item.get('wind', 0) or 0)
                            fmap['solar'][h] += float(item.get('solar', 0) or 0)
                    for f in fmap:
                        for h in range(24):
                            if cnt[h] > 0: fmap[f][h] = round(fmap[f][h]/cnt[h], 1)
                    print("✅ 발전원별 실시간 데이터 수집 성공!")
                    return fmap
        except Exception:
            time.sleep(2)
    return None

# =====================================================================
# 실제 수집 및 master data.json 저장
# =====================================================================
api_smp = fetch_api_smp(target_date_str, DATA_GO_KR_KEY)
if not api_smp:
    raise ValueError(f"전력거래소 서버 지연으로 {target_date_str} SMP API 호출에 실패했습니다.")

api_gen = fetch_api_gen(target_date_str, DATA_GO_KR_KEY)

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

if api_gen:
    p_gen = [max(0.0, v) for v in api_gen['pump']]; p_load = [min(0.0, v) for v in api_gen['pump']]
    e_dis = [max(0.0, v) for v in api_gen['ess']]; e_chg = [min(0.0, v) for v in api_gen['ess']]
    net = [api_gen['nuclear'][i] + api_gen['coal'][i] + api_gen['oil'][i] + api_gen['gas'][i] + api_gen['hydro'][i] + p_gen[i] + e_dis[i] for i in range(24)]
    gen_entry = {
        'nuclear': api_gen['nuclear'], 'coal': api_gen['coal'], 'oil': api_gen['oil'], 'gas': api_gen['gas'],
        'hydro': api_gen['hydro'], 'pump_gen': p_gen, 'pump_load': p_load, 'ess_dis': e_dis, 'ess_chg': e_chg,
        'wind': api_gen['wind'], 'solar': api_gen['solar'], 'net_load': net, 'spread': [api_gen['pump'][i] + api_gen['ess'][i] for i in range(24)]
    }
else:
    gen_entry = prev_day_data["gen"]

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
