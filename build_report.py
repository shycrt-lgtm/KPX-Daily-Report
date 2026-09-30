import os
import json
import subprocess
import time
import urllib.parse
import requests
import sys
from datetime import datetime, timedelta, timezone

print(">> SPA master data.json 일일 업데이트 시작...")

DATA_FILE = "data.json"

# 인증키는 코드에 적지 않고 GitHub Secret(DATA_GO_KR_KEY)에서만 읽음 (공개 저장소이므로 코드에 키 기재 금지)
API_KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
if not API_KEY:
    print("🚨 DATA_GO_KR_KEY 가 비어 있습니다. GitHub 저장소 Settings > Secrets and variables > Actions 에 등록하세요.")
    sys.exit(1)

if not os.path.exists(DATA_FILE):
    raise FileNotFoundError("data.json 파일이 없습니다.")

with open(DATA_FILE, "r", encoding="utf-8") as f:
    master = json.load(f)

KST = timezone(timedelta(hours=9))
target_dt = datetime.now(KST) - timedelta(days=1)
target_date_str = target_dt.strftime("%Y%m%d")
weekday_kr_list = ["월", "화", "수", "목", "금", "토", "일"]

default_gen = {
    'nuclear': [0]*24, 'coal': [0]*24, 'oil': [0]*24, 'gas': [0]*24, 'hydro': [0]*24, 
    'pump_gen': [0]*24, 'pump_load': [0]*24, 'ess_dis': [0]*24, 'ess_chg': [0]*24, 
    'wind': [0]*24, 'solar': [0]*24, 'net_load': [0]*24, 'spread': [0]*24
}

def build_day_payload(d_str, land_smp, prev_day_data):
    d_dt = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=KST)
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
    
    recent_7 = [{
        'date': f"{d_dt.month}.{d_dt.day}({weekday_kr_list[d_dt.weekday()]})",
        'is_holiday': d_dt.weekday() in [5, 6] or d_str in master["holidays"],
        'avg': smp_avg, 'max': smp_max, 'min': smp_min,
        'cap': prev_day_data.get("cap_peak", 68420) + 20000, 'peak': prev_day_data.get("cap_peak", 68420), 'time': prev_day_data.get("cap_time", "19:00"), 'res': prev_day_data.get("cap_res", 36.8)
    }]
    for delta in range(1, 7):
        p_dt = d_dt - timedelta(days=delta)
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

    return {
        'smp_hourly': land_smp, 'smp_avg': smp_avg, 'smp_max': smp_max, 'smp_min': smp_min,
        'smp_max_idx': max_idx, 'smp_min_idx': min_idx, 'peak_band': f"{left+1}~{right+1}시", 'peak_left': left, 'peak_right': right,
        'cap_peak': prev_day_data.get("cap_peak", 68420), 'cap_time': prev_day_data.get("cap_time", "19:00"), 'cap_res': prev_day_data.get("cap_res", 36.8),
        'lng_price': 1058.34, 'diff_lng_text': "전월비 +88.73원", 'diff_lng_color': "text-rose-600", 'prev_smp_str': "147.88원/kWh",
        'diff_avg_txt': diff_avg_txt, 'diff_avg_color': diff_avg_color,
        'diff_max_txt': diff_max_txt, 'diff_max_color': diff_max_color,
        'diff_min_txt': diff_min_txt, 'diff_min_color': diff_min_color,
        'recent_7': recent_7, 'gen': prev_day_data.get("gen", default_gen)
    }

# 1. 28일 완벽 복구
if "20260928" not in master["days"]:
    print(">> [복구] 28일 데이터 누락 감지, 공식 실적으로 즉시 생성합니다.")
    kpx_28 = [100.28, 97.45, 97.45, 97.45, 97.45, 97.45, 102.40, 107.07, 107.28, 107.24, 106.86, 107.23, 106.49, 107.23, 108.30, 112.47, 130.69, 130.69, 130.69, 130.69, 130.69, 130.69, 127.91, 115.49]
    prev_27 = master["days"].get("20260927", {})
    master["days"]["20260928"] = build_day_payload("20260928", kpx_28, prev_27)
    master["latest_date"] = "20260928"
    
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False)
    print(">> [복구] 28일 데이터 정상 디스크 기록 완료.")

# 2. 전일 SMP 수집 (한국전력거래소_계통한계가격 및 수요예측, 공공데이터포털)
API_URL = "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand"


def http_get_text(params, attempts=3, use_curl_fallback=True):
    """API 호출. 접속 시간 초과 등 통신 오류는 대기 후 재시도하고, 마지막에 curl(IPv4)로도 시도.
    (status, text) 를 반환하며 끝내 실패하면 None."""
    last = None
    for i in range(1, attempts + 1):
        try:
            r = requests.get(API_URL, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=(20, 60))
            if r.status_code < 500 and r.status_code != 429:
                return r.status_code, r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}"
        print(f">> [통신 실패 {i}/{attempts}] {last}")
        if i < attempts:
            time.sleep(10 * i)

    if use_curl_fallback:
        print(">> [대체 호출] curl(IPv4)로 재시도합니다.")
        url = API_URL + "?" + urllib.parse.urlencode(params)
        try:
            res = subprocess.run(
                ["curl", "-sS", "-4", "--connect-timeout", "20", "--max-time", "60",
                 "-H", "User-Agent: Mozilla/5.0", "-w", "\n%{http_code}", url],
                capture_output=True, text=True, timeout=90)
            if res.returncode == 0 and res.stdout:
                body, _, code = res.stdout.rpartition("\n")
                return int(code or 0), body
            print(f">> [curl 실패] exit={res.returncode} {res.stderr[:200].replace(API_KEY, '***')}")
        except Exception as e:
            print(f">> [curl 에러] {type(e).__name__}")
    return None


def fetch_land_smp(trade_date, attempts=3, use_curl_fallback=True):
    """해당 일자 육지 SMP 24시간 리스트를 반환. 실패 시 None (원인은 로그에 출력)."""
    params = {"serviceKey": API_KEY, "pageNo": 1, "numOfRows": 100, "dataType": "json", "date": trade_date}
    print(f">> [API 호출] {trade_date} 육지 SMP 수집 중...")
    got = http_get_text(params, attempts, use_curl_fallback)
    if got is None:
        print(f">> [통신 에러] {trade_date} 호출이 모든 시도에서 실패했습니다.")
        return None
    status, text = got
    print(f">> [API 상태] HTTP {status}")
    try:
        data = json.loads(text)
    except ValueError:
        print(">> [응답 형식 오류] JSON이 아님:", text[:300].replace(API_KEY, "***"))
        return None

    root = data.get("response", data) if isinstance(data, dict) else {}
    header = root.get("header", {}) if isinstance(root, dict) else {}
    body = root.get("body", {}) if isinstance(root, dict) else {}
    print(f">> [API 결과] code={header.get('resultCode')} msg={header.get('resultMsg')} totalCount={body.get('totalCount')}")

    items = body.get("items", {}) if isinstance(body, dict) else {}
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        print(">> [데이터 없음] 응답 일부:", str(data)[:300].replace(API_KEY, "***"))
        return None

    areas = sorted({str(x.get("areaName", "")) for x in items})
    print(f">> [지역 구분] {areas}")
    land = [x for x in items if "육지" in str(x.get("areaName", ""))]
    if not land:
        print(">> [지역 오류] 육지 행을 찾지 못했습니다.")
        return None

    by_hour = {}
    for x in land:
        try:
            by_hour[int(x["hour"])] = float(x["smp"])
        except (KeyError, ValueError, TypeError):
            continue
    hours = sorted(by_hour)
    if hours != list(range(1, 25)) and hours != list(range(0, 24)):
        print(f">> [시간 오류] 24시간이 모두 있지 않습니다. 수신 시간대: {hours}")
        return None
    return [by_hour[h] for h in hours]


def report_check_vs_master(smp_list, d_str):
    """API 값이 기존 확정값(KPX 파일 기반)과 같은지 로그로만 확인 (실패 처리 안 함)."""
    old = master["days"].get(d_str, {}).get("smp_hourly")
    if not old or len(old) != 24:
        return
    diff = max(abs(a - b) for a, b in zip(smp_list, old))
    if diff < 0.01:
        print(f">> [검증] {d_str} API 값이 기존 확정값과 일치합니다.")
    else:
        print(f">> [검증 주의] {d_str} API 값이 기존 확정값과 다릅니다 (최대 차이 {diff:.2f}원). 하루 전 계획값일 수 있습니다.")


api_smp = fetch_land_smp(target_date_str)

if not api_smp:
    print(f"🚨 {target_date_str} SMP 수집 실패. 위 로그의 원인을 확인하세요. (data.json은 변경하지 않음)")
    sys.exit(1)

# 전일 값 검증(로그 전용)
prev_date_str = (target_dt - timedelta(days=1)).strftime("%Y%m%d")
prev_api = fetch_land_smp(prev_date_str, attempts=1, use_curl_fallback=False)
if prev_api:
    report_check_vs_master(prev_api, prev_date_str)
else:
    print(">> [검증 생략] 전일 비교용 호출이 실패하여 확정값 비교는 건너뜁니다.")

# 3. 수집 성공 시 저장
prev_day = master["days"].get(prev_date_str, {})
master["days"][target_date_str] = build_day_payload(target_date_str, api_smp, prev_day)
master["latest_date"] = target_date_str

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(master, f, ensure_ascii=False)

print(f"🎉 {target_date_str} SMP 반영 완료")
print(">> [참고] 발전원별 발전량·최대부하·공급예비율·LNG 단가는 아직 전일 값을 유지합니다 (다음 단계에서 API 연동).")
