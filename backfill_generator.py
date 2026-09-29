import os
import glob
import json
import pandas as pd
from datetime import datetime, timedelta

print(">> 과거 엑셀/CSV 데이터로부터 정밀 data.json 생성 시작...")

def read_flexible_csv(file_path):
    for enc in ['cp949', 'utf-8-sig', 'euc-kr', 'utf-8']:
        try: return pd.read_csv(file_path, encoding=enc)
        except: continue
    raise ValueError(f"파일을 읽을 수 없습니다: {file_path}")

# 1. SMP 로딩
smp_candidates = glob.glob("*SMP*.csv") + glob.glob("*SMP*.xlsx")
df_smp = read_flexible_csv(smp_candidates[0]) if smp_candidates[0].endswith('.csv') else pd.read_excel(smp_candidates[0])
df_smp['일자'] = pd.to_datetime(df_smp['기간']).dt.strftime('%Y%m%d')
smp_dict = {str(r['일자']): {'hourly': [float(str(r[f"{i:02d}시"]).replace(',','')) for i in range(1,25)], 'max': float(str(r['최대']).replace(',','')), 'min': float(str(r['최소']).replace(',','')), 'avg': float(str(r['가중평균']).replace(',',''))} for _, r in df_smp.iterrows()}

monthly_smp = {}
for ym in set(k[:6] for k in smp_dict.keys()):
    vals = [v['avg'] for k, v in smp_dict.items() if k.startswith(ym)]
    if vals: monthly_smp[ym] = round(sum(vals)/len(vals), 2)

# 2. 최대부하 로딩
cap_candidates = glob.glob("*최대부하*.csv") + glob.glob("*최대부하*.xlsx")
df_cap = read_flexible_csv(cap_candidates[0]) if cap_candidates[0].endswith('.csv') else pd.read_excel(cap_candidates[0])
df_cap['일자'] = df_cap.apply(lambda r: f"{int(r['년']):04d}{int(r['월']):02d}{int(r['일']):02d}", axis=1)
cap_dict = {str(r['일자']): {'cap': int(float(str(r['공급능력(MW)']).replace(',',''))), 'peak': int(float(str(r['최대전력(MW)']).replace(',',''))), 'res': float(str(r['공급예비율(%)']).replace(',','')), 'time': str(r['최대전력기준일시']).split('(')[-1].replace(')','') if '(' in str(r['최대전력기준일시']) else "19:00"} for _, r in df_cap.iterrows()}

# 3. LNG 단가 정밀 로딩 (합계 칸의 우측 1월~12월 지정 파싱)
lng_dict = {}
lng_candidates = glob.glob("*LNG*.xlsx") + [f for f in glob.glob("*.xlsx") if "5." in f]
if lng_candidates:
    try:
        df_lng = pd.read_excel(lng_candidates[0], sheet_name="입방당", header=None)
        current_year = None
        for r in range(len(df_lng)):
            row = [str(x).strip() for x in df_lng.iloc[r]]
            for cell in row:
                c = cell.replace('.0','')
                if c.isdigit() and len(c) == 4 and int(c) in [2022, 2023, 2024, 2025, 2026]:
                    current_year = int(c)
            
            if current_year and any("합계" in s for s in row):
                try:
                    # '합계' 글자가 있는 칸의 인덱스를 찾음
                    hap_idx = next(i for i, s in enumerate(row) if "합계" in s)
                    # 합계 칸 바로 우측 12개 칸이 1월~12월 단가
                    months_vals = df_lng.iloc[r, hap_idx+1 : hap_idx+13]
                    for m_idx, val in enumerate(months_vals):
                        try:
                            v = float(str(val).replace(',','').strip())
                            if v > 0:
                                lng_dict[f"{current_year}{m_idx+1:02d}"] = round(v, 2)
                        except: pass
                except Exception as e:
                    pass
    except Exception as e:
        print(f"LNG 엑셀 읽기 알림: {e}")

lng_dict["202609"] = 1058.34

# 4. 발전원별 발전량
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

common_dates = sorted(list(set(smp_dict.keys()) & set(gen_dict.keys()) & set(cap_dict.keys())))
latest_date_str = common_dates[-1]

korean_holidays = {
    '20220101': '신정', '20220131': '설날연휴', '20220201': '설날', '20220202': '설날연휴', '20220301': '삼일절', '20220309': '대통령선거', '20220505': '어린이날', '20220508': '부처님오신날', '20220601': '지방선거', '20220606': '현충일', '20220815': '광복절', '20220909': '추석연휴', '20220910': '추석', '20220911': '추석연휴', '20220912': '대체공휴일', '20221003': '개천절', '20221009': '한글날', '20221010': '대체공휴일', '20221225': '성탄절',
    '20230101': '신정', '20230121': '설날연휴', '20230122': '설날', '20230123': '설날연휴', '20230124': '대체공휴일', '20230301': '삼일절', '20230505': '어린이날', '20230527': '부처님오신날', '20230529': '대체공휴일', '20230606': '현충일', '20230815': '광복절', '20230928': '추석연휴', '20230929': '추석', '20230930': '추석연휴', '20231002': '임시공휴일', '20231003': '개천절', '20231009': '한글날', '20231225': '성탄절',
    '20240101': '신정', '20240209': '설날연휴', '20240210': '설날', '20240211': '설날연휴', '20240212': '대체공휴일', '20240301': '삼일절', '20240410': '국회의원선거', '20240505': '어린이날', '20240506': '대체공휴일', '20240515': '부처님오신날', '20240606': '현충일', '20240815': '광복절', '20240916': '추석연휴', '20240917': '추석', '20240918': '추석연휴', '20241001': '임시공휴일(국군의날)', '20241003': '개천절', '20241009': '한글날', '20241225': '성탄절',
    '20250101': '신정', '20250128': '설날연휴', '20250129': '설날', '20250130': '설날연휴', '20250301': '삼일절', '20250303': '대체공휴일', '20250505': '어린이날', '20250506': '대체공휴일', '20250606': '현충일', '20250815': '광복절', '20251003': '개천절', '20251005': '추석연휴', '20251006': '추석', '20251007': '추석연휴', '20251008': '대체공휴일', '20251009': '한글날', '20251225': '성탄절',
    '20260101': '신정', '20260216': '설날연휴', '20260217': '설날', '20260218': '설날연휴', '20260301': '삼일절', '20260302': '대체공휴일', '20260505': '어린이날', '20260524': '부처님오신날', '20260525': '대체공휴일', '20260603': '지방선거', '20260606': '현충일', '20260815': '광복절', '20260817': '대체공휴일', '20260924': '추석연휴', '20260925': '추석', '20260926': '추석연휴', '20261003': '개천절', '20261005': '대체공휴일', '20261009': '한글날', '20261225': '성탄절'
}

weekday_kr_list = ["월", "화", "수", "목", "금", "토", "일"]

def fmt_diff(v):
    if v > 0: return f"+{abs(v):.2f}원", "text-rose-600"
    elif v < 0: return f"▼{abs(v):.2f}원", "text-blue-600"
    return "- 0.00원", "text-slate-500"

days_payload = {}
for target_date_str in common_dates:
    dt = datetime.strptime(target_date_str, "%Y%m%d")
    ym_str = target_date_str[:6]
    first_day = dt.replace(day=1)
    prev_ym_str = (first_day - timedelta(days=1)).strftime("%Y%m")
    
    lng_price = lng_dict.get(ym_str, 1058.34)
    prev_lng = lng_dict.get(prev_ym_str, lng_price)
    d_lng = round(lng_price - prev_lng, 2)
    diff_lng_text = f"전월비 +{abs(d_lng):.2f}원" if d_lng > 0 else (f"전월비 ▼{abs(d_lng):.2f}원" if d_lng < 0 else "전월비 변동없음")
    diff_lng_color = "text-rose-600" if d_lng > 0 else ("text-blue-600" if d_lng < 0 else "text-slate-500")
    prev_smp_str = f"{monthly_smp.get(prev_ym_str, 0.0):.2f}원/kWh"
    
    s_info = smp_dict[target_date_str]
    land_smp = s_info['hourly']
    smp_avg, smp_max, smp_min = s_info['avg'], s_info['max'], s_info['min']
    max_idx, min_idx = land_smp.index(smp_max), land_smp.index(smp_min)
    
    thresh = smp_min + (smp_max - smp_min) * 0.85
    left, right = max_idx, max_idx
    while left > 0 and land_smp[left-1] >= thresh: left -= 1
    while right < 23 and land_smp[right+1] >= thresh: right += 1
    if (right - left) > 6: left = max(0, max_idx - 2); right = min(23, max_idx + 2)
    
    recent_7 = []
    for delta in range(7):
        p_d = (dt - timedelta(days=delta)).strftime("%Y%m%d")
        if p_d in smp_dict and p_d in cap_dict:
            p_dt = datetime.strptime(p_d, "%Y%m%d")
            p_hol = p_dt.weekday() in [5, 6] or p_d in korean_holidays
            recent_7.append({
                'date': f"{p_dt.month}.{p_dt.day}({weekday_kr_list[p_dt.weekday()]})",
                'is_holiday': p_hol,
                'avg': smp_dict[p_d]['avg'], 'max': smp_dict[p_d]['max'], 'min': smp_dict[p_d]['min'],
                'cap': cap_dict[p_d]['cap'], 'peak': cap_dict[p_d]['peak'], 'time': cap_dict[p_d]['time'], 'res': cap_dict[p_d]['res']
            })
            
    diff_avg_txt, diff_avg_color = fmt_diff(round(smp_avg - recent_7[1]['avg'], 2)) if len(recent_7) > 1 else ("-", "text-slate-400")
    diff_max_txt, diff_max_color = fmt_diff(round(smp_max - recent_7[1]['max'], 2)) if len(recent_7) > 1 else ("-", "text-slate-400")
    diff_min_txt, diff_min_color = fmt_diff(round(smp_min - recent_7[1]['min'], 2)) if len(recent_7) > 1 else ("-", "text-slate-400")
    
    days_payload[target_date_str] = {
        'smp_hourly': land_smp, 'smp_avg': smp_avg, 'smp_max': smp_max, 'smp_min': smp_min,
        'smp_max_idx': max_idx, 'smp_min_idx': min_idx, 'peak_band': f"{left+1}~{right+1}시",
        'peak_left': left, 'peak_right': right,
        'cap_peak': cap_dict[target_date_str]['peak'], 'cap_time': cap_dict[target_date_str]['time'], 'cap_res': cap_dict[target_date_str]['res'],
        'lng_price': lng_price, 'diff_lng_text': diff_lng_text, 'diff_lng_color': diff_lng_color, 'prev_smp_str': prev_smp_str,
        'diff_avg_txt': diff_avg_txt, 'diff_avg_color': diff_avg_color,
        'diff_max_txt': diff_max_txt, 'diff_max_color': diff_max_color,
        'diff_min_txt': diff_min_txt, 'diff_min_color': diff_min_color,
        'recent_7': recent_7, 'gen': gen_dict[target_date_str]
    }

master_obj = {
    'latest_date': latest_date_str,
    'holidays': korean_holidays,
    'days': days_payload
}

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(master_obj, f, ensure_ascii=False)

print(f">> 완료: 과거 전체({len(common_dates)}일)의 월별 LNG 단가가 정확히 반영된 data.json 생성 성공!")
