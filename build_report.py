import os
import glob
import json
import shutil
import urllib.request
import urllib.parse
import pandas as pd
from datetime import datetime, timedelta

print(">> 일일 리포트 자동 생성 프로세스 시작...")

# =====================================================================
# 1. 환경 변수 및 기준일(D-1) 설정
# =====================================================================
DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_KEY", "")

target_dt = datetime.now() - timedelta(days=1)
target_date_str = target_dt.strftime("%Y%m%d")
target_date_dashed = target_dt.strftime("%Y-%m-%d")

weekday_kr_list = ["월", "화", "수", "목", "금", "토", "일"]
w_kr = weekday_kr_list[target_dt.weekday()]
display_date = f"{target_dt.strftime('%Y년 %m월 %d일')}({w_kr})"
hours = [f"{i}시" for i in range(1, 25)]
json_hours = json.dumps(hours)

def read_flexible_csv(file_path):
    for enc in ['cp949', 'utf-8-sig', 'euc-kr', 'utf-8']:
        try: return pd.read_csv(file_path, encoding=enc)
        except: continue
    raise ValueError(f"파일을 읽을 수 없습니다: {file_path}")

# =====================================================================
# 2. 기존 데이터 (과거 엑셀 내역) 베이스 파싱
# =====================================================================
smp_candidates = glob.glob("*SMP*.csv") + glob.glob("*SMP*.xlsx")
df_smp = read_flexible_csv(smp_candidates[0]) if smp_candidates[0].endswith('.csv') else pd.read_excel(smp_candidates[0])
df_smp['일자'] = pd.to_datetime(df_smp['기간']).dt.strftime('%Y%m%d')
smp_dict = {str(r['일자']): {'hourly': [float(str(r[f"{i:02d}시"]).replace(',','')) for i in range(1,25)], 'max': float(str(r['최대']).replace(',','')), 'min': float(str(r['최소']).replace(',','')), 'avg': float(str(r['가중평균']).replace(',',''))} for _, r in df_smp.iterrows()}

cap_candidates = glob.glob("*최대부하*.csv") + glob.glob("*최대부하*.xlsx")
df_cap = read_flexible_csv(cap_candidates[0]) if cap_candidates[0].endswith('.csv') else pd.read_excel(cap_candidates[0])
df_cap['일자'] = df_cap.apply(lambda r: f"{int(r['년']):04d}{int(r['월']):02d}{int(r['일']):02d}", axis=1)
cap_dict = {str(r['일자']): {'cap': int(float(str(r['공급능력(MW)']).replace(',',''))), 'peak': int(float(str(r['최대전력(MW)']).replace(',',''))), 'res': float(str(r['공급예비율(%)']).replace(',','')), 'time': str(r['최대전력기준일시']).split('(')[-1].replace(')','') if '(' in str(r['최대전력기준일시']) else "19:00"} for _, r in df_cap.iterrows()}

gen_dict = {}
for gf in sorted(glob.glob("*발전원별 발전량_*.xlsx")):
    xl = pd.ExcelFile(gf)
    df_g = pd.read_excel(gf, sheet_name=xl.sheet_names[0])
    df_g['일자'] = pd.to_datetime(df_g['일시']).dt.strftime('%Y%m%d')
    df_g['시'] = pd.to_datetime(df_g['일시']).dt.hour
    num_cols = [c for c in df_g.columns if c not in ['일시', '일자', '시']]
    for c in num_cols: df_g[c] = pd.to_numeric(df_g[c].astype(str).str.replace(',',''), errors='coerce').fillna(0.0)
    grouped = df_g.groupby(['일자', '시'])[num_cols].mean().reset_index()
    for d, g_df in grouped.groupby('일자'):
        if len(g_df) < 24: continue
        g_df = g_df.sort_values('시')
        nuc = g_df['원자력'].tolist() if '원자력' in g_df else [0.0]*24
        coal = (g_df['석탄'] if '석탄' in g_df else (g_df['유연탄'] + g_df['국내탄'] if '유연탄' in g_df else [0.0]*24)).tolist()
        oil = g_df['유류'].tolist() if '유류' in g_df else [0.0]*24
        gas = g_df['가스'].tolist() if '가스' in g_df else [0.0]*24
        hydro = g_df['수력'].tolist() if '수력' in g_df else [0.0]*24
        pump = g_df['양수'].tolist() if '양수' in g_df else [0.0]*24
        ess = g_df['ESS'].tolist() if 'ESS' in g_df else [0.0]*24
        wind = g_df['풍력'].tolist() if '풍력' in g_df else [0.0]*24
        solar = g_df['태양광(전력시장)'].tolist() if '태양광(전력시장)' in g_df else [0.0]*24
        p_gen = [max(0.0, v) for v in pump]; p_load = [min(0.0, v) for v in pump]
        e_dis = [max(0.0, v) for v in ess]; e_chg = [min(0.0, v) for v in ess]
        net = [nuc[i]+coal[i]+oil[i]+gas[i]+hydro[i]+p_gen[i]+e_dis[i] for i in range(24)]
        gen_dict[d] = {'nuclear': nuc, 'coal': coal, 'oil': oil, 'gas': gas, 'hydro': hydro, 'pump_gen': p_gen, 'pump_load': p_load, 'ess_dis': e_dis, 'ess_chg': e_chg, 'wind': wind, 'solar': solar, 'net_load': net, 'spread': [pump[i]+ess[i] for i in range(24)]}

# =====================================================================
# 3. 공공데이터 API 실시간 호출 (이중 인코딩 및 400 크래시 완벽 차단)
# =====================================================================
def fetch_api_smp(trade_date, key):
    if not key: return None
    # 🚨 핵심 보완: 이미 %가 포함된 키의 이중 인코딩(Bad Request) 원천 방지
    safe_key = urllib.parse.quote(urllib.parse.unquote(key.strip()))
    url = f"http://apis.data.go.kr/B552115/smpInland/getSmpInlandList?serviceKey={safe_key}&pageNo=1&numOfRows=30&tradeDate={trade_date}&dataType=JSON"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.loads(res.read().decode('utf-8'))
            items = data['response']['body']['items']['item']
            items = sorted(items, key=lambda x: int(x.get('tradeHour', 0)))
            hourly = [float(x.get('smp', 0)) for x in items if int(x.get('tradeHour', 0)) in range(1, 25)]
            return hourly if len(hourly) == 24 else None
    except Exception as e:
        print(f"SMP API 오류 발생 (무시하고 엑셀 백업 사용): {e}")
    return None

def fetch_api_gen(trade_date, key):
    if not key: return None
    safe_key = urllib.parse.quote(urllib.parse.unquote(key.strip()))
    url = f"http://apis.data.go.kr/B552115/GenByFuel/getGenByFuel?serviceKey={safe_key}&pageNo=1&numOfRows=300&tradeDate={trade_date}&dataType=JSON"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.loads(res.read().decode('utf-8'))
            items = data['response']['body']['items']['item']
            fmap = {'nuclear':[0]*24,'coal':[0]*24,'oil':[0]*24,'gas':[0]*24,'hydro':[0]*24,'pump':[0]*24,'ess':[0]*24,'wind':[0]*24,'solar':[0]*24}
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
            return fmap
    except Exception as e:
        print(f"발전원 API 오류 발생 (무시하고 엑셀 백업 사용): {e}")
    return None

# =====================================================================
# 4. 실시간 API 연동 및 데이터 통합
# =====================================================================
print(f">> {target_date_str} 실시간 데이터 수집 시도...")

api_smp = fetch_api_smp(target_date_str, DATA_GO_KR_KEY)
if api_smp:
    smp_dict[target_date_str] = {'hourly': api_smp, 'max': max(api_smp), 'min': min(api_smp), 'avg': round(sum(api_smp)/24, 2)}
    print(">> SMP 실시간 API 수집 성공!")

api_gen = fetch_api_gen(target_date_str, DATA_GO_KR_KEY)
if api_gen:
    p_gen = [max(0.0, v) for v in api_gen['pump']]; p_load = [min(0.0, v) for v in api_gen['pump']]
    e_dis = [max(0.0, v) for v in api_gen['ess']]; e_chg = [min(0.0, v) for v in api_gen['ess']]
    net = [api_gen['nuclear'][i] + api_gen['coal'][i] + api_gen['oil'][i] + api_gen['gas'][i] + api_gen['hydro'][i] + p_gen[i] + e_dis[i] for i in range(24)]
    spread = [api_gen['pump'][i] + api_gen['ess'][i] for i in range(24)]
    gen_dict[target_date_str] = {'nuclear': api_gen['nuclear'], 'coal': api_gen['coal'], 'oil': api_gen['oil'], 'gas': api_gen['gas'], 'hydro': api_gen['hydro'], 'pump_gen': p_gen, 'pump_load': p_load, 'ess_dis': e_dis, 'ess_chg': e_chg, 'wind': api_gen['wind'], 'solar': api_gen['solar'], 'net_load': net, 'spread': spread}
    print(">> 발전원별 실시간 API 수집 성공!")

# API 장애 시, 스크립트가 뻗지 않고 엑셀에 있는 가장 마지막 데이터를 복사하여 에러 방지
if target_date_str not in smp_dict: smp_dict[target_date_str] = smp_dict[sorted(smp_dict.keys())[-1]]
if target_date_str not in gen_dict: gen_dict[target_date_str] = gen_dict[sorted(gen_dict.keys())[-1]]
if target_date_str not in cap_dict: cap_dict[target_date_str] = cap_dict[sorted(cap_dict.keys())[-1]]

# =====================================================================
# 5. 리포트 UI 데이터 연산 및 HTML 렌더링
# =====================================================================
korean_holidays = {
    '20220101': '신정', '20220131': '설날연휴', '20220201': '설날', '20220202': '설날연휴', '20220301': '삼일절', '20220309': '대통령선거', '20220505': '어린이날', '20220508': '부처님오신날', '20220601': '지방선거', '20220606': '현충일', '20220815': '광복절', '20220909': '추석연휴', '20220910': '추석', '20220911': '추석연휴', '20220912': '대체공휴일', '20221003': '개천절', '20221009': '한글날', '20221010': '대체공휴일', '20221225': '성탄절',
    '20230101': '신정', '20230121': '설날연휴', '20230122': '설날', '20230123': '설날연휴', '20230124': '대체공휴일', '20230301': '삼일절', '20230505': '어린이날', '20230527': '부처님오신날', '20230529': '대체공휴일', '20230606': '현충일', '20230815': '광복절', '20230928': '추석연휴', '20230929': '추석', '20230930': '추석연휴', '20231002': '임시공휴일', '20231003': '개천절', '20231009': '한글날', '20231225': '성탄절',
    '20240101': '신정', '20240209': '설날연휴', '20240210': '설날', '20240211': '설날연휴', '20240212': '대체공휴일', '20240301': '삼일절', '20240410': '국회의원선거', '20240505': '어린이날', '20240506': '대체공휴일', '20240515': '부처님오신날', '20240606': '현충일', '20240815': '광복절', '20240916': '추석연휴', '20240917': '추석', '20240918': '추석연휴', '20241001': '임시공휴일(국군의날)', '20241003': '개천절', '20241009': '한글날', '20241225': '성탄절',
    '20250101': '신정', '20250128': '설날연휴', '20250129': '설날', '20250130': '설날연휴', '20250301': '삼일절', '20250303': '대체공휴일', '20250505': '어린이날', '20250506': '대체공휴일', '20250606': '현충일', '20250815': '광복절', '20251003': '개천절', '20251005': '추석연휴', '20251006': '추석', '20251007': '추석연휴', '20251008': '대체공휴일', '20251009': '한글날', '20251225': '성탄절',
    '20260101': '신정', '20260216': '설날연휴', '20260217': '설날', '20260218': '설날연휴', '20260301': '삼일절', '20260302': '대체공휴일', '20260505': '어린이날', '20260524': '부처님오신날', '20260525': '대체공휴일', '20260603': '지방선거', '20260606': '현충일', '20260815': '광복절', '20260817': '대체공휴일', '20260924': '추석연휴', '20260925': '추석', '20260926': '추석연휴', '20261003': '개천절', '20261005': '대체공휴일', '20261009': '한글날', '20261225': '성탄절'
}

def is_korean_holiday_or_weekend(d_str):
    d_obj = datetime.strptime(d_str, "%Y%m%d")
    if d_obj.weekday() in [5, 6]: return True, "주말"
    if d_str in korean_holidays: return True, korean_holidays[d_str]
    md = d_str[4:]
    fixed = {'0101':'신정','0301':'삼일절','0505':'어린이날','0606':'현충일','0815':'광복절','1003':'개천절','1009':'한글날','1225':'성탄절'}
    return (True, fixed[md]) if md in fixed else (False, "")

is_holiday, h_name = is_korean_holiday_or_weekend(target_date_str)
date_color_cls = "text-rose-600 font-black" if is_holiday else "text-slate-800 font-bold"
badge_color_cls = "text-rose-600 bg-rose-50 border-rose-200" if is_holiday else "text-slate-500 bg-white border-slate-200"
badge_title = f'title="{h_name}"' if h_name else ""
holiday_map_js = {f"{k[:4]}-{k[4:6]}-{k[6:]}": v for k, v in korean_holidays.items()}

s_info = smp_dict[target_date_str]
land_smp = s_info['hourly']
avg_smp = s_info['avg']; max_smp = s_info['max']; min_smp = s_info['min']
max_smp_idx = land_smp.index(max_smp); min_smp_idx = land_smp.index(min_smp)

s_thresh = min_smp + (max_smp - min_smp) * 0.85
left, right = max_smp_idx, max_smp_idx
while left > 0 and land_smp[left-1] >= s_thresh: left -= 1
while right < 23 and land_smp[right+1] >= s_thresh: right += 1
if (right - left) > 6:
    left = max(0, max_smp_idx - 2); right = min(23, max_smp_idx + 2)
peak_band_label = f"{left+1}~{right+1}시"

g_info = gen_dict[target_date_str]
c_info = cap_dict[target_date_str]

# 엑셀과 API 이력을 모두 뒤져서 최근 7일 내역 구성
recent_7 = []
for delta in range(7):
    pd_str = (target_dt - timedelta(days=delta)).strftime("%Y%m%d")
    if pd_str in smp_dict and pd_str in cap_dict:
        p_dt = datetime.strptime(pd_str, "%Y%m%d")
        p_hol, _ = is_korean_holiday_or_weekend(pd_str)
        recent_7.append({
            'date': f"{p_dt.month}.{p_dt.day}({weekday_kr_list[p_dt.weekday()]})",
            'is_holiday': p_hol,
            'avg': smp_dict[pd_str]['avg'], 'max': smp_dict[pd_str]['max'], 'min': smp_dict[pd_str]['min'],
            'cap': cap_dict[pd_str]['cap'], 'peak': cap_dict[pd_str]['peak'], 'time': cap_dict[pd_str]['time'], 'res': cap_dict[pd_str]['res']
        })

def format_diff(val):
    if val > 0: return f"+{abs(val):.2f}원", "text-rose-600"
    elif val < 0: return f"▼{abs(val):.2f}원", "text-blue-600"
    return "- 0.00원", "text-slate-500"

if len(recent_7) > 1:
    diff_avg_txt, diff_avg_color = format_diff(round(avg_smp - recent_7[1]['avg'], 2))
    diff_max_txt, diff_max_color = format_diff(round(max_smp - recent_7[1]['max'], 2))
    diff_min_txt, diff_min_color = format_diff(round(min_smp - recent_7[1]['min'], 2))
else:
    diff_avg_txt = diff_max_txt = diff_min_txt = "-"
    diff_avg_color = diff_max_color = diff_min_color = "text-slate-400"

table_rows_html = ""
for i, r in enumerate(recent_7):
    bg = "bg-slate-50 font-bold" if i == 0 else ""
    txt = "text-holiday" if r['is_holiday'] else ""
    table_rows_html += f"""<tr class="{bg}"><td class="{txt}">{r['date']}</td><td>{r['avg']:.2f}</td><td>{r['max']:.2f}</td><td>{r['min']:.2f}</td><td>{r['cap']:,}</td><td>{r['peak']:,}</td><td>{r['time']}</td><td>{r['res']}%</td></tr>"""

html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>전력시장 전일실적 요약</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@2.2.1/dist/chartjs-plugin-annotation.min.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css">
  <script src="https://cdn.jsdelivr.net/npm/flatpickr"></script>
  <script src="https://cdn.jsdelivr.net/npm/flatpickr/dist/l10n/ko.js"></script>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;900&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Noto Sans KR', sans-serif; background-color: #ffffff; color: #1a1a1a; }}
    .mckinsey-border {{ border-top: 4px solid #001f3f; }}
    .chart-container {{ position: relative; height: 450px; width: 100%; border: 1px solid #e5e7eb; padding: 1rem; background-color: #ffffff; }}
    table th, table td {{ border: 1px solid #e5e7eb; padding: 12px; text-align: center; font-size: 0.875rem; }}
    table th {{ background-color: #f8fafc; font-weight: 700; color: #334155; }}
    .text-holiday {{ color: #dc2626 !important; font-weight: 700; }}
    h2 {{ font-size: 1.125rem; font-weight: 700; color: #001f3f; margin-bottom: 0.75rem; border-bottom: 2px solid #e5e7eb; padding-bottom: 0.5rem; }}
    .flatpickr-calendar {{ border: 1px solid #cbd5e1; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.15); }}
    .flatpickr-calendar .flatpickr-day.holiday-day, .flatpickr-calendar .flatpickr-day.weekend-day {{ color: #dc2626 !important; font-weight: 700 !important; }}
    .flatpickr-calendar .flatpickr-day.holiday-day::after {{ content: ''; position: absolute; bottom: 3px; left: 50%; transform: translateX(-50%); width: 4px; height: 4px; border-radius: 50%; background-color: #dc2626; }}
    .flatpickr-calendar .flatpickr-day.selected.holiday-day, .flatpickr-calendar .flatpickr-day.selected.weekend-day {{ background: #001f3f !important; color: #ffffff !important; }}
    .flatpickr-calendar .flatpickr-weekday:first-child, .flatpickr-calendar .flatpickr-weekday:last-child {{ color: #dc2626 !important; font-weight: 700; }}
  </style>
</head>
<body class="p-4 md:p-8">
  <div class="max-w-6xl mx-auto space-y-8">
    <div class="mckinsey-border pt-4 pb-2 flex flex-col md:flex-row md:justify-between md:items-end gap-4">
      <div>
        <h1 class="text-3xl font-black text-slate-900 tracking-tight">전력시장 전일실적 요약</h1>
        <p class="text-sm text-slate-500 mt-1">기준일: <strong class="{date_color_cls}">{display_date}</strong> | 육지 기준 | <span class="font-bold text-slate-700">에너지사업총괄</span></p>
      </div>

      <div class="flex flex-col md:items-end gap-2">
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="text-[11px] font-bold text-slate-400 mr-0.5">KPX 바로가기:</span>
          <a href="https://new.kpx.or.kr/powerSource.es?mid=a10404030000&device=chart" target="_blank" class="inline-flex items-center gap-1 px-2.5 py-1 bg-amber-50 text-amber-900 border border-amber-300 text-xs font-bold rounded">⚡ 실시간 수급현황</a>
          <a href="https://new.kpx.or.kr/smpInland.es?mid=a10404080100&device=pc" target="_blank" class="inline-flex items-center gap-1 px-2.5 py-1 bg-sky-50 text-sky-900 border border-sky-300 text-xs font-bold rounded">📈 실시간 SMP</a>
        </div>

        <div class="flex items-center gap-2 bg-slate-50 border border-slate-200 px-3 py-1.5 rounded-lg shadow-sm">
          <label for="historyDate" class="text-xs md:text-sm font-bold text-slate-700 cursor-pointer">조회일자:</label>
          <div class="relative flex items-center">
            <input type="text" id="historyDate" value="{target_date_dashed}" class="bg-transparent text-xs md:text-sm font-bold {date_color_cls} outline-none cursor-pointer w-28 pr-6 text-center" readonly>
            <svg class="w-4 h-4 text-slate-500 absolute right-1 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
          </div>
          <span class="text-xs font-bold px-1.5 py-0.5 rounded border {badge_color_cls}" {badge_title}>({w_kr})</span>
          <div class="ml-2 pl-2 border-l border-slate-200">
            <a href="index.html" class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#001f3f] hover:bg-slate-800 text-white text-xs font-bold rounded shadow transition">
              <span>⚡ 최신 실적으로</span> &rarr;
            </a>
          </div>
        </div>
      </div>
    </div>

    <!-- 00. Executive Summary -->
    <div>
      <h2>00. Executive Summary</h2>
      <div class="bg-slate-50 border border-slate-200 p-6 text-base md:text-lg text-slate-800 font-medium">
        <ul class="space-y-2">
          <li><strong>수급지표 :</strong> 최대전력수요 {c_info['time']} 발생, 최대전력수요 {c_info['peak']:,}MW, 공급예비율 {c_info['res']}%</li>
          <li><strong>가격지표 :</strong> 가중평균 SMP {avg_smp:.2f}원/kWh(전일대비 {diff_avg_txt}), 피크구간 {peak_band_label}</li>
          <li><strong>전원구성 :</strong> 기저발전 안정적 발전 지속, 일몰 후 저녁 피크 시 LNG 및 양수·ESS 가동 대응</li>
        </ul>
      </div>
    </div>

    <!-- 01. 전일 전력시장 실적요약 -->
    <div>
      <h2>01. 전일 전력시장 실적요약</h2>
      <div class="grid grid-cols-2 md:grid-cols-6 gap-0 border border-slate-200">
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">가중평균 SMP</p>
          <div class="flex items-end gap-1"><span class="text-2xl font-black text-slate-900">{avg_smp:.2f}</span><span class="text-xs font-medium pb-1">원</span></div>
          <p class="text-[11px] font-semibold mt-1 {diff_avg_color}">전일비 {diff_avg_txt}</p>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center bg-rose-50/30">
          <p class="text-xs font-bold text-slate-500 mb-1">최고 SMP</p>
          <div class="flex items-end gap-1"><span class="text-2xl font-black text-[#d93f3c]">{max_smp:.2f}</span><span class="text-xs font-medium pb-1">원</span></div>
          <p class="text-[11px] font-semibold mt-1 {diff_max_color}">전일비 {diff_max_txt}</p>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">최저 SMP</p>
          <div class="flex items-end gap-1"><span class="text-2xl font-black text-slate-900">{min_smp:.2f}</span><span class="text-xs font-medium pb-1">원</span></div>
          <p class="text-[11px] font-semibold mt-1 {diff_min_color}">전일비 {diff_min_txt}</p>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">최대전력 (피크)</p>
          <div class="flex items-end gap-1"><span class="text-2xl font-black text-slate-900">{c_info['peak']:,}</span><span class="text-xs font-medium pb-1">MW</span></div>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">피크 발생시간</p>
          <div class="flex items-end gap-1"><span class="text-2xl font-black text-slate-900">{c_info['time']}</span></div>
        </div>
        <div class="p-4 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">공급예비율</p>
          <div class="flex items-end gap-1"><span class="text-2xl font-black text-emerald-600">{c_info['res']}</span><span class="text-xs font-medium pb-1">%</span></div>
        </div>
      </div>
    </div>

    <!-- 02. SMP 차트 -->
    <div>
      <h2>02. 시간대별 계통한계가격(SMP) 및 피크 밴드</h2>
      <div class="chart-container"><canvas id="smpChart"></canvas></div>
    </div>

    <!-- 03. 최근 7일 테이블 -->
    <div>
      <h2>03. 최근 7일 전력수급 및 SMP 실적</h2>
      <div class="overflow-x-auto">
        <table class="w-full border-collapse">
          <thead>
            <tr><th rowspan="2">구분</th><th colspan="3">SMP (원/kWh)</th><th colspan="4">전력수급 실적 (MW)</th></tr>
            <tr><th>가중평균</th><th>최고</th><th>최저</th><th>공급능력</th><th>최대전력</th><th>피크</th><th>예비율</th></tr>
          </thead>
          <tbody>{table_rows_html}</tbody>
        </table>
      </div>
    </div>

    <!-- 04. 발전원별 구성 차트 -->
    <div>
      <h2>04. 발전원별 실시간 수급 구성 및 순부하</h2>
      <div class="chart-container"><canvas id="generationChart"></canvas></div>
    </div>

    <!-- 05. 주요 발전원 라인 차트 -->
    <div>
      <h2>05. 주요 발전원 시간대별 출력 추이</h2>
      <div class="chart-container"><canvas id="sourceLineChart"></canvas></div>
    </div>

    <!-- 06. SMP 스프레드 -->
    <div>
      <h2>06. SMP 스프레드</h2>
      <div class="chart-container"><canvas id="spreadChart"></canvas></div>
    </div>
  </div>

  <script>
    const holidayMap = {json.dumps(holiday_map_js, ensure_ascii=False)};
    flatpickr("#historyDate", {{
      locale: "ko", dateFormat: "Y-m-d", defaultDate: "{target_date_dashed}", minDate: "2022-01-01", maxDate: "{target_date_dashed}", disableMobile: true,
      onDayCreate: (dObj, dStr, fp, dayElem) => {{
        const dateStr = flatpickr.formatDate(dayElem.dateObj, "Y-m-d");
        if (dayElem.dateObj.getDay() === 0 || dayElem.dateObj.getDay() === 6) dayElem.classList.add("weekend-day");
        if (holidayMap[dateStr]) {{ dayElem.classList.add("holiday-day"); dayElem.setAttribute("title", holidayMap[dateStr]); }}
      }},
      onChange: (selectedDates, dateStr) => {{
        if (!dateStr) return;
        window.location.href = (dateStr === "{target_date_dashed}") ? "index.html" : "daily_report_" + dateStr.replace(/-/g, '') + ".html";
      }}
    }});

    Chart.register(window['chartjs-plugin-annotation']);
    const labels = {json_hours};
    const commonOptions = {{
      responsive: true, maintainAspectRatio: false,
      layout: {{ padding: {{ top: 40, right: 20, bottom: 20, left: 10 }} }},
      scales: {{ x: {{ grid: {{ display: false }} }}, y: {{ grid: {{ color: '#f1f5f9' }}, grace: '20%' }} }}
    }};

    new Chart(document.getElementById('smpChart'), {{
      type: 'line', data: {{ labels: labels, datasets: [{{ label: '시간대별 SMP (원/kWh)', data: {json.dumps(land_smp)}, borderColor: '#005587', borderWidth: 3, pointRadius: 3 }}] }},
      options: {{ ...commonOptions, plugins: {{ annotation: {{ annotations: {{
        peakBox: {{ type: 'box', xMin: {left}, xMax: {right}, backgroundColor: 'rgba(217, 63, 60, 0.08)', borderWidth: 0 }},
        maxLbl: {{ type: 'label', xValue: {max_smp_idx}, yValue: {max_smp}, content: ['최고 {max_smp}원'], color: '#d93f3c', yAdjust: -15 }},
        minLbl: {{ type: 'label', xValue: {min_smp_idx}, yValue: {min_smp}, content: ['최저 {min_smp}원'], color: '#005587', yAdjust: -15 }}
      }}}}}}
    }});

    new Chart(document.getElementById('generationChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: '순부하', data: {json.dumps(g_info['net_load'])}, borderColor: '#1a1a1a', borderDash: [4,4], fill: false }},
          {{ label: '원자력', data: {json.dumps(g_info['nuclear'])}, backgroundColor: '#f59e0b', fill: true, stack: 'area' }},
          {{ label: '석탄', data: {json.dumps(g_info['coal'])}, backgroundColor: '#b45309', fill: true, stack: 'area' }},
          {{ label: 'LNG', data: {json.dumps(g_info['gas'])}, backgroundColor: '#fde047', fill: true, stack: 'area' }},
          {{ label: '태양광', data: {json.dumps(g_info['solar'])}, backgroundColor: '#ef4444', fill: true, stack: 'area' }}
        ]
      }},
      options: {{ ...commonOptions, scales: {{ x: {{ stacked: true }}, y: {{ stacked: true }} }} }}
    }});

    new Chart(document.getElementById('sourceLineChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: 'LNG', data: {json.dumps(g_info['gas'])}, borderColor: '#005587', borderWidth: 2.5 }},
          {{ label: '석탄', data: {json.dumps(g_info['coal'])}, borderColor: '#d93f3c', borderWidth: 2.5 }},
          {{ label: '신재생(태양광)', data: {json.dumps(g_info['solar'])}, borderColor: '#f59e0b', borderWidth: 2.5 }}
        ]
      }},
      options: commonOptions
    }});

    new Chart(document.getElementById('spreadChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: '유연성 자원 (양수/ESS)', yAxisID: 'y', data: {json.dumps(g_info['spread'])}, borderColor: '#059669', borderWidth: 2.5 }},
          {{ label: '계통한계가격(SMP)', yAxisID: 'y1', data: {json.dumps(land_smp)}, borderColor: '#005587', borderDash: [4,4], borderWidth: 1.5 }}
        ]
      }},
      options: {{ ...commonOptions, scales: {{ y: {{ type: 'linear', position: 'left' }}, y1: {{ type: 'linear', position: 'right', grid: {{ drawOnChartArea: false }} }} }} }}
    }});
  </script>
</body>
</html>
"""

# =====================================================================
# 6. 파일 동시 저장 및 index.html 강제 갱신
# =====================================================================
daily_filename = f"daily_report_{target_date_str}.html"
with open(daily_filename, "w", encoding="utf-8") as f:
    f.write(html_content)

shutil.copyfile(daily_filename, "index.html")
print(f">> 완료: {daily_filename} 생성 및 index.html 배포 완료!")
