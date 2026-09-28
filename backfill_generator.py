import os
import glob
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

print(">> 데이터 파일 로딩 시작...")

def read_flexible_csv(file_path):
    for enc in ['cp949', 'utf-8-sig', 'euc-kr', 'utf-8']:
        try:
            return pd.read_csv(file_path, encoding=enc)
        except Exception:
            continue
    raise ValueError(f"파일을 읽을 수 없습니다: {file_path}")

# 1. 시간대별 SMP 로딩
smp_candidates = glob.glob("*SMP*.csv") + glob.glob("*SMP*.xlsx")
if not smp_candidates:
    raise FileNotFoundError("SMP 파일을 찾을 수 없습니다.")
smp_file = smp_candidates[0]

if smp_file.endswith('.csv'):
    df_smp = read_flexible_csv(smp_file)
else:
    df_smp = pd.read_excel(smp_file)

df_smp['일자'] = pd.to_datetime(df_smp['기간']).dt.strftime('%Y%m%d')
smp_dict = {}
for _, row in df_smp.iterrows():
    d = str(row['일자'])
    smp_dict[d] = {
        'hourly': [float(str(row[f"{i:02d}시"]).replace(',', '')) for i in range(1, 25)],
        'max': float(str(row['최대']).replace(',', '')),
        'min': float(str(row['최소']).replace(',', '')),
        'avg': float(str(row['가중평균']).replace(',', ''))
    }
print(f"SMP 로딩 완료: {len(smp_dict)}일")

# 1-1. 월별 가중평균 SMP 계산 (전월 평균 SMP 산출용)
monthly_smp_dict = {}
smp_months = set(k[:6] for k in smp_dict.keys())
for ym in smp_months:
    month_days = [v['avg'] for k, v in smp_dict.items() if k.startswith(ym)]
    if month_days:
        monthly_smp_dict[ym] = round(sum(month_days) / len(month_days), 2)
print(f"월별 평균 SMP 집계 완료: {len(monthly_smp_dict)}개월")

# 2. 일별 최대부하 및 예비율 로딩
cap_candidates = glob.glob("*최대부하*.csv") + glob.glob("*최대부하*.xlsx")
if not cap_candidates:
    raise FileNotFoundError("최대부하 파일을 찾을 수 없습니다.")
cap_file = cap_candidates[0]

if cap_file.endswith('.csv'):
    df_cap = read_flexible_csv(cap_file)
else:
    df_cap = pd.read_excel(cap_file)

df_cap['일자'] = df_cap.apply(lambda r: f"{int(r['년']):04d}{int(r['월']):02d}{int(r['일']):02d}", axis=1)
cap_dict = {}
for _, row in df_cap.iterrows():
    d = str(row['일자'])
    time_str = str(row['최대전력기준일시'])
    peak_hour = time_str.split('(')[-1].replace(')', '') if '(' in time_str else "19:00"
    cap_dict[d] = {
        'cap': int(float(str(row['공급능력(MW)']).replace(',', ''))),
        'peak': int(float(str(row['최대전력(MW)']).replace(',', ''))),
        'res': float(str(row['공급예비율(%)']).replace(',', '')),
        'time': peak_hour
    }
print(f"수급실적 로딩 완료: {len(cap_dict)}일")

# 3. LNG 단가 로딩
lng_dict = {}
lng_candidates = glob.glob("*LNG*.xlsx") + [f for f in glob.glob("*.xlsx") if "5." in f]
if lng_candidates:
    lng_file = lng_candidates[0]
    print(f"LNG 단가 파일 로딩 중: {lng_file}")
    df_lng = pd.read_excel(lng_file, sheet_name="입방당", header=None)
    
    month_cols = {}
    for r in range(min(15, len(df_lng))):
        row_vals = [str(x).replace('.0','').replace('월','').strip() for x in df_lng.iloc[r]]
        found = {m: row_vals.index(str(m)) for m in range(1, 13) if str(m) in row_vals}
        if len(found) >= 10:
            month_cols = found
            break
            
    current_year = None
    for r in range(len(df_lng)):
        row_strs = [str(x).replace(' ', '') for x in df_lng.iloc[r]]
        
        for cell in row_strs:
            c_clean = cell.replace('.0', '')
            if c_clean.isdigit() and len(c_clean) == 4 and int(c_clean) >= 2000:
                current_year = int(c_clean)
                break
                
        if any("합계" in c for c in row_strs) and current_year:
            for m, c_idx in month_cols.items():
                val = df_lng.iloc[r, c_idx]
                try:
                    v = float(str(val).replace(',', '').strip())
                    if v > 0:
                        lng_dict[f"{current_year}{m:02d}"] = v
                except:
                    pass
print(f"LNG 단가 로딩 완료: {len(lng_dict)}개월 데이터 확보")

# 4. 발전원별 발전량 연도별 로딩 및 집계
gen_dict = {}
gen_files = sorted(glob.glob("*발전원별 발전량_*.xlsx"))
for gf in gen_files:
    print(f"발전량 파싱 중: {gf}...")
    xl = pd.ExcelFile(gf)
    df_g = pd.read_excel(gf, sheet_name=xl.sheet_names[0])
    df_g['일시'] = pd.to_datetime(df_g['일시'])
    df_g['일자'] = df_g['일시'].dt.strftime('%Y%m%d')
    df_g['시'] = df_g['일시'].dt.hour
    
    numeric_cols = [c for c in df_g.columns if c not in ['일시', '일자', '시']]
    for c in numeric_cols:
        df_g[c] = pd.to_numeric(df_g[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0.0)
        
    grouped = df_g.groupby(['일자', '시'])[numeric_cols].mean().reset_index()
    
    for d, g_df in grouped.groupby('일자'):
        if len(g_df) < 24:
            continue
        g_df = g_df.sort_values('시')
        
        nuc = g_df['원자력'].tolist() if '원자력' in g_df else [0.0]*24
        coal = (g_df['석탄'] if '석탄' in g_df else (g_df['유연탄'] + g_df['국내탄'] if '유연탄' in g_df and '국내탄' in g_df else [0.0]*24)).tolist()
        oil = g_df['유류'].tolist() if '유류' in g_df else [0.0]*24
        gas = g_df['가스'].tolist() if '가스' in g_df else [0.0]*24
        hydro = g_df['수력'].tolist() if '수력' in g_df else [0.0]*24
        pump = g_df['양수'].tolist() if '양수' in g_df else [0.0]*24
        ess = g_df['ESS'].tolist() if 'ESS' in g_df else [0.0]*24
        wind = g_df['풍력'].tolist() if '풍력' in g_df else [0.0]*24
        solar = g_df['태양광(전력시장)'].tolist() if '태양광(전력시장)' in g_df else [0.0]*24
        
        pump_gen = [max(0.0, v) for v in pump]
        pump_load = [min(0.0, v) for v in pump]
        ess_dis = [max(0.0, v) for v in ess]
        ess_chg = [min(0.0, v) for v in ess]
        
        net_ld = [nuc[i] + coal[i] + oil[i] + gas[i] + hydro[i] + pump_gen[i] + ess_dis[i] for i in range(24)]
        spread = [pump[i] + ess[i] for i in range(24)]
        
        gen_dict[d] = {
            'nuclear': [round(v, 1) for v in nuc],
            'coal': [round(v, 1) for v in coal],
            'oil': [round(v, 1) for v in oil],
            'gas': [round(v, 1) for v in gas],
            'hydro': [round(v, 1) for v in hydro],
            'pump_gen': [round(v, 1) for v in pump_gen],
            'pump_load': [round(v, 1) for v in pump_load],
            'ess_dis': [round(v, 1) for v in ess_dis],
            'ess_chg': [round(v, 1) for v in ess_chg],
            'wind': [round(v, 1) for v in wind],
            'solar': [round(v, 1) for v in solar],
            'net_load': [round(v, 1) for v in net_ld],
            'spread': [round(v, 1) for v in spread]
        }

print(f"발전원 집계 완료: {len(gen_dict)}일")

common_dates = sorted(list(set(smp_dict.keys()) & set(gen_dict.keys()) & set(cap_dict.keys())))
latest_date_str = common_dates[-1]
latest_dt = datetime.strptime(latest_date_str, "%Y%m%d")
latest_date_dashed = latest_dt.strftime("%Y-%m-%d")
print(f">> 최종 생성 대상 일수: {len(common_dates)}일 (최신일자: {latest_date_str})")

# 대한민국 법정 공휴일 및 대체공휴일 마스터 딕셔너리 (2022~2027+)
korean_holidays = {
    # 2022년
    '20220101': '신정', '20220131': '설날연휴', '20220201': '설날', '20220202': '설날연휴',
    '20220301': '삼일절', '20220309': '대통령선거', '20220505': '어린이날', '20220508': '부처님오신날',
    '20220601': '지방선거', '20220606': '현충일', '20220815': '광복절', '20220909': '추석연휴',
    '20220910': '추석', '20220911': '추석연휴', '20220912': '대체공휴일', '20221003': '개천절',
    '20221009': '한글날', '20221010': '대체공휴일', '20221225': '성탄절',
    # 2023년
    '20230101': '신정', '20230121': '설날연휴', '20230122': '설날', '20230123': '설날연휴',
    '20230124': '대체공휴일', '20230301': '삼일절', '20230505': '어린이날', '20230527': '부처님오신날',
    '20230529': '대체공휴일', '20230606': '현충일', '20230815': '광복절', '20230928': '추석연휴',
    '20230929': '추석', '20230930': '추석연휴', '20231002': '임시공휴일', '20231003': '개천절',
    '20231009': '한글날', '20231225': '성탄절',
    # 2024년
    '20240101': '신정', '20240209': '설날연휴', '20240210': '설날', '20240211': '설날연휴',
    '20240212': '대체공휴일', '20240301': '삼일절', '20240410': '국회의원선거', '20240505': '어린이날',
    '20240506': '대체공휴일', '20240515': '부처님오신날', '20240606': '현충일', '20240815': '광복절',
    '20240916': '추석연휴', '20240917': '추석', '20240918': '추석연휴', '20241001': '임시공휴일(국군의날)',
    '20241003': '개천절', '20241009': '한글날', '20241225': '성탄절',
    # 2025년
    '20250101': '신정', '20250128': '설날연휴', '20250129': '설날', '20250130': '설날연휴',
    '20250301': '삼일절', '20250303': '대체공휴일', '20250505': '어린이날', '20250506': '대체공휴일',
    '20250606': '현충일', '20250815': '광복절', '20251003': '개천절', '20251005': '추석연휴',
    '20251006': '추석', '20251007': '추석연휴', '20251008': '대체공휴일', '20251009': '한글날',
    '20251225': '성탄절',
    # 2026년
    '20260101': '신정', '20260216': '설날연휴', '20260217': '설날', '20260218': '설날연휴',
    '20260301': '삼일절', '20260302': '대체공휴일', '20260505': '어린이날', '20260524': '부처님오신날',
    '20260525': '대체공휴일', '20260603': '지방선거', '20260606': '현충일', '20260815': '광복절',
    '20260817': '대체공휴일', '20260924': '추석연휴', '20260925': '추석', '20260926': '추석연휴',
    '20261003': '개천절', '20261005': '대체공휴일', '20261009': '한글날', '20261225': '성탄절',
    # 2027년
    '20270101': '신정', '20270206': '설날연휴', '20270207': '설날', '20270208': '설날연휴',
    '20270209': '대체공휴일', '20270301': '삼일절', '20270303': '대통령선거', '20270505': '어린이날',
    '20270513': '부처님오신날', '20270606': '현충일', '20270815': '광복절', '20270816': '대체공휴일',
    '20270914': '추석연휴', '20270915': '추석', '20270916': '추석연휴', '20271003': '개천절',
    '20271004': '대체공휴일', '20271009': '한글날', '20271011': '대체공휴일', '20271225': '성탄절'
}

# 공휴일 및 주말 판별 헬퍼 (미래 연도 고정공휴일 자동 판별 포함)
def is_korean_holiday_or_weekend(d_str):
    d_obj = datetime.strptime(d_str, "%Y%m%d")
    # 주말 (토=5, 일=6)
    if d_obj.weekday() in [5, 6]:
        return True, "주말"
    if d_str in korean_holidays:
        return True, korean_holidays[d_str]
    # 미래 연도 고정 공휴일 자동 대응
    md = d_str[4:]
    fixed_holidays = {
        '0101': '신정', '0301': '삼일절', '0505': '어린이날', '0606': '현충일',
        '0815': '광복절', '1003': '개천절', '1009': '한글날', '1225': '성탄절'
    }
    if md in fixed_holidays:
        return True, fixed_holidays[md]
    return False, ""

# 클라이언트 자바스크립트 달력용 공휴일 리스트 (YYYY-MM-DD)
holiday_dashed_list = [f"{k[:4]}-{k[4:6]}-{k[6:]}" for k in korean_holidays.keys()]
json_holidays_js = json.dumps(holiday_dashed_list)

hours = [f"{i}시" for i in range(1, 25)]
json_hours = json.dumps(hours)
weekday_kr_list = ["월", "화", "수", "목", "금", "토", "일"]

# 변동 표기 헬퍼 (상승: +, 하락: ▼)
def format_diff(val):
    if val > 0:
        return f"+{abs(val):.2f}원", "text-rose-600"
    elif val < 0:
        return f"▼{abs(val):.2f}원", "text-blue-600"
    else:
        return "- 0.00원", "text-slate-500"

for target_date_str in common_dates:
    dt = datetime.strptime(target_date_str, "%Y%m%d")
    w_kr = weekday_kr_list[dt.weekday()]
    is_holiday, h_name = is_korean_holiday_or_weekend(target_date_str)
    
    # 주말 및 공휴일이면 날짜와 배지를 빨간색으로 표기
    date_color_cls = "text-rose-600" if is_holiday else "text-slate-800"
    badge_color_cls = "text-rose-600 bg-rose-50 border-rose-200" if is_holiday else "text-slate-500 bg-white border-slate-200"
    badge_title = f'title="{h_name}"' if h_name else ""
    display_date = f"{dt.strftime('%Y년 %m월 %d일')}({w_kr})"
    target_date_dashed = dt.strftime("%Y-%m-%d")
    
    ym_str = target_date_str[:6]
    first_of_month = dt.replace(day=1)
    prev_month_dt = first_of_month - timedelta(days=1)
    prev_ym_str = prev_month_dt.strftime("%Y%m")
    
    lng_price = lng_dict.get(ym_str, 0.0)
    prev_lng_price = lng_dict.get(prev_ym_str, 0.0)
    prev_month_smp = monthly_smp_dict.get(prev_ym_str, 0.0)
    
    if lng_price > 0 and prev_lng_price > 0:
        diff_lng_val = round(lng_price - prev_lng_price, 2)
        if diff_lng_val > 0:
            diff_lng_text = f"전월비 +{abs(diff_lng_val):.2f}원"
            diff_lng_color = "text-rose-600"
        elif diff_lng_val < 0:
            diff_lng_text = f"전월비 ▼{abs(diff_lng_val):.2f}원"
            diff_lng_color = "text-blue-600"
        else:
            diff_lng_text = "전월비 변동없음"
            diff_lng_color = "text-slate-500"
    else:
        diff_lng_text = "-"
        diff_lng_color = "text-slate-400"

    s_info = smp_dict[target_date_str]
    land_smp = s_info['hourly']
    avg_smp = s_info['avg']
    max_smp = s_info['max']
    min_smp = s_info['min']
    max_smp_idx = land_smp.index(max_smp)
    min_smp_idx = land_smp.index(min_smp)
    
    # 피크 밴드 보정 (중심점 기준 최대 5~6시간)
    smp_thresh = min_smp + (max_smp - min_smp) * 0.85
    left = max_smp_idx
    while left > 0 and land_smp[left-1] >= smp_thresh: left -= 1
    right = max_smp_idx
    while right < 23 and land_smp[right+1] >= smp_thresh: right += 1
    
    if (right - left) > 6:
        left = max(0, max_smp_idx - 2)
        right = min(23, max_smp_idx + 2)
        
    p_start = left
    p_end = right
    peak_band_label = f"{p_start+1}~{p_end+1}시"
    
    g_info = gen_dict[target_date_str]
    c_info = cap_dict[target_date_str]
    max_peak_actual = c_info['peak']
    peak_hour_str = c_info['time']
    reserve_ratio = c_info['res']
    
    recent_7 = []
    for delta in range(7):
        prev_d = (dt - timedelta(days=delta)).strftime("%Y%m%d")
        if prev_d in smp_dict and prev_d in cap_dict:
            p_dt = datetime.strptime(prev_d, "%Y%m%d")
            p_is_hol, _ = is_korean_holiday_or_weekend(prev_d)
            recent_7.append({
                'date': f"{p_dt.month}.{p_dt.day}({weekday_kr_list[p_dt.weekday()]})",
                'is_holiday': p_is_hol,
                'avg': smp_dict[prev_d]['avg'],
                'max': smp_dict[prev_d]['max'],
                'min': smp_dict[prev_d]['min'],
                'cap': cap_dict[prev_d]['cap'],
                'peak': cap_dict[prev_d]['peak'],
                'time': cap_dict[prev_d]['time'],
                'res': cap_dict[prev_d]['res']
            })
            
    if len(recent_7) > 1:
        diff_avg_val = round(avg_smp - recent_7[1]['avg'], 2)
        diff_max_val = round(max_smp - recent_7[1]['max'], 2)
        diff_min_val = round(min_smp - recent_7[1]['min'], 2)
        
        diff_avg_txt, diff_avg_color = format_diff(diff_avg_val)
        diff_max_txt, diff_max_color = format_diff(diff_max_val)
        diff_min_txt, diff_min_color = format_diff(diff_min_val)
    else:
        diff_avg_txt, diff_avg_color = "-", "text-slate-400"
        diff_max_txt, diff_max_color = "-", "text-slate-400"
        diff_min_txt, diff_min_color = "-", "text-slate-400"
    
    # 03번 최근 7일 테이블 (주말 및 공휴일 모두 빨간색 처리)
    table_rows_html = ""
    for i, r in enumerate(recent_7):
        bg = "bg-slate-50 font-bold" if i == 0 else ""
        txt = "text-holiday" if r['is_holiday'] else ""
        table_rows_html += f"""
        <tr class="{bg}">
            <td class="{txt}">{r['date']}</td>
            <td>{r['avg']:.2f}</td><td>{r['max']:.2f}</td><td>{r['min']:.2f}</td>
            <td>{r['cap']:,}</td><td>{r['peak']:,}</td><td>{r['time']}</td><td>{r['res']}%</td>
        </tr>
        """
        
    prev_smp_str = f"{prev_month_smp:.2f}원/kWh" if prev_month_smp > 0 else "-"
    
    # 00. Executive Summary
    ai_summary = f"""
    <ul class="space-y-2">
      <li><strong>수급지표 :</strong> 최대전력수요 {peak_hour_str} 발생, 최대전력수요 {max_peak_actual:,}MW, 공급예비율 {reserve_ratio:.1f}%</li>
      <li>
        <strong>가격지표 :</strong> 가중평균 SMP {avg_smp:.2f}원/kWh(전일대비 {diff_avg_txt}), 피크구간 {peak_band_label}<br>
        <span class="pl-4 text-slate-600 block mt-1">- 당월 적용 LNG 단가: {lng_price:,.2f}원/Nm³({diff_lng_text}), 전월 평균 SMP: {prev_smp_str}</span>
      </li>
      <li><strong>전원구성 :</strong> 기저발전(원자력·석탄) 안정적 발전 지속, 주간 태양광 출력 집중으로 순부하 최저 형성 후 저녁 피크 시 LNG 및 양수·ESS 가동 대응</li>
    </ul>
    """
    ai_gen_summary = f"주간 태양광 발전량 증가로 순부하 최저점을 형성하였으며, 일몰 후 저녁 피크 램핑 수요를 LNG 및 양수/ESS가 안정적으로 전담함."

    # 최신 실적 이동 버튼
    latest_button_html = f"""
      <a href="daily_report_{latest_date_str}.html" 
         class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-[#001f3f] hover:bg-slate-800 text-white text-xs font-bold rounded shadow transition" title="확정된 가장 최근 실적일로 이동">
        <span>⚡ 최신 실적({latest_dt.strftime('%m.%d')})으로</span> &rarr;
      </a>
    """

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>전력시장 전일실적 요약</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@2.2.1/dist/chartjs-plugin-annotation.min.js"></script>
  
  <!-- 공휴일/주말 빨간색 렌더링용 Flatpickr 달력 플러그인 -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css">
  <script src="https://cdn.jsdelivr.net/npm/flatpickr"></script>
  <script src="https://cdn.jsdelivr.net/npm/flatpickr/dist/l10n/ko.js"></script>

  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Noto Sans KR', sans-serif; background-color: #ffffff; color: #1a1a1a; }}
    .mckinsey-border {{ border-top: 4px solid #001f3f; }}
    .chart-container {{ position: relative; height: 450px; width: 100%; border: 1px solid #e5e7eb; padding: 1rem; background-color: #ffffff; }}
    table th, table td {{ border: 1px solid #e5e7eb; padding: 12px; text-align: center; font-size: 0.875rem; }}
    table th {{ background-color: #f8fafc; font-weight: 700; color: #334155; }}
    .text-holiday {{ color: #dc2626 !important; font-weight: 700; }}
    h2 {{ font-size: 1.125rem; font-weight: 700; color: #001f3f; margin-bottom: 0.75rem; border-bottom: 2px solid #e5e7eb; padding-bottom: 0.5rem; }}
    .summary-box li {{ margin-bottom: 0.5rem; }}

    /* 달력 팝업 내 일요일, 토요일, 대한민국 공휴일 빨간색 강제 적용 */
    .flatpickr-calendar .flatpickr-day.holiday-day,
    .flatpickr-calendar .flatpickr-day.weekend-day {{
      color: #dc2626 !important;
      font-weight: 700 !important;
    }}
    .flatpickr-calendar .flatpickr-day.selected.holiday-day,
    .flatpickr-calendar .flatpickr-day.selected.weekend-day {{
      background: #001f3f !important;
      color: #ffffff !important;
    }}
    .flatpickr-calendar .flatpickr-weekday:first-child,
    .flatpickr-calendar .flatpickr-weekday:last-child {{
      color: #dc2626 !important;
      font-weight: 700;
    }}
  </style>
</head>
<body class="p-4 md:p-8">
  <div class="max-w-6xl mx-auto space-y-8">
    <!-- 헤더 영역 -->
    <div class="mckinsey-border pt-4 pb-2 flex flex-col md:flex-row md:justify-between md:items-end gap-4">
      <div>
        <h1 class="text-3xl font-black text-slate-900 tracking-tight">전력시장 전일실적 요약</h1>
        <p class="text-sm text-slate-500 mt-1">기준일: <strong class="{date_color_cls}">{display_date}</strong> | 육지 기준 | <span class="font-bold text-slate-700">에너지사업총괄</span></p>
      </div>

      <div class="flex flex-col md:items-end gap-2">
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="text-[11px] font-bold text-slate-400 mr-0.5">KPX 실시간 바로가기:</span>
          <a href="https://new.kpx.or.kr/powerSource.es?mid=a10404030000&device=chart" target="_blank" rel="noopener noreferrer"
             class="inline-flex items-center gap-1 px-2.5 py-1 bg-amber-50 hover:bg-amber-100 text-amber-900 border border-amber-300 text-xs font-bold rounded shadow-xs transition" title="KPX 실시간 발전원별 수급현황">
            <span>⚡ 실시간 수급현황</span>
            <svg class="w-3 h-3 text-amber-700" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
          </a>
          <a href="https://new.kpx.or.kr/smpInland.es?mid=a10404080100&device=pc" target="_blank" rel="noopener noreferrer"
             class="inline-flex items-center gap-1 px-2.5 py-1 bg-sky-50 hover:bg-sky-100 text-sky-900 border border-sky-300 text-xs font-bold rounded shadow-xs transition" title="KPX 당일 시간대별 SMP">
            <span>📈 실시간 SMP</span>
            <svg class="w-3 h-3 text-sky-700" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
          </a>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          {latest_button_html}
          <div class="flex items-center gap-2 bg-slate-50 border border-slate-200 px-3 py-1.5 rounded-lg shadow-sm">
            <label for="historyDate" class="text-xs md:text-sm font-bold text-slate-700 cursor-pointer">조회일자:</label>
            <input type="text" id="historyDate" value="{target_date_dashed}" 
                   class="bg-transparent text-xs md:text-sm font-bold {date_color_cls} outline-none cursor-pointer w-24 text-center" readonly>
            <span class="text-xs font-bold px-1.5 py-0.5 rounded border {badge_color_cls}" {badge_title}>({w_kr})</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 00. Executive Summary -->
    <div>
      <h2>00. Executive Summary</h2>
      <div class="bg-slate-50 border border-slate-200 p-6 text-base md:text-lg text-slate-800 leading-relaxed font-medium summary-box">
        {ai_summary}
      </div>
    </div>

    <!-- 01. 전일 전력시장 실적요약 -->
    <div>
      <h2>01. 전일 전력시장 실적요약</h2>
      <div class="grid grid-cols-2 md:grid-cols-7 gap-0 border border-slate-200">
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center bg-slate-50">
          <p class="text-xs font-bold text-slate-500 mb-1">LNG 단가 (당월)</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-800">{lng_price:,.2f}</span><span class="text-[11px] font-medium pb-1">원/Nm³</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 {diff_lng_color}">{diff_lng_text}</p>
          <p class="text-[10px] text-slate-500 mt-0.5 font-medium">전월평균 SMP: <span class="font-bold text-slate-700">{prev_smp_str}</span></p>
        </div>

        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">가중평균 SMP</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-900">{avg_smp:.2f}</span><span class="text-xs font-medium pb-1">원</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 {diff_avg_color}">전일비 {diff_avg_txt}</p>
        </div>

        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center bg-rose-50/30">
          <p class="text-xs font-bold text-slate-500 mb-1">최고 SMP</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-[#d93f3c]">{max_smp:.2f}</span><span class="text-xs font-medium pb-1">원</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 {diff_max_color}">전일비 {diff_max_txt}</p>
        </div>

        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">최저 SMP</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-900">{min_smp:.2f}</span><span class="text-xs font-medium pb-1">원</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 {diff_min_color}">전일비 {diff_min_txt}</p>
        </div>

        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">최대전력 (피크)</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-900">{max_peak_actual:,}</span><span class="text-xs font-medium pb-1">MW</span>
          </div>
        </div>

        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">피크 발생시간</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-900">{peak_hour_str}</span>
          </div>
        </div>

        <div class="p-4 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">공급예비율</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-emerald-600">{reserve_ratio:.1f}</span><span class="text-xs font-medium pb-1">%</span>
          </div>
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
            <tr>
              <th rowspan="2">구분</th><th colspan="3">SMP (원/kWh)</th><th colspan="4">전력수급 실적 (MW)</th>
            </tr>
            <tr>
              <th>가중평균</th><th>최고</th><th>최저</th><th>공급능력</th><th>최대전력</th><th>피크</th><th>예비율</th>
            </tr>
          </thead>
          <tbody>{table_rows_html}</tbody>
        </table>
      </div>
    </div>

    <!-- 04. 발전원별 구성 차트 -->
    <div>
      <h2>04. 발전원별 실시간 수급 구성 및 순부하</h2>
      <div class="chart-container"><canvas id="generationChart"></canvas></div>
      <div class="mt-3 bg-slate-50 border border-slate-200 p-4 rounded text-sm text-slate-800 font-medium">
        💡 <strong>분석 의견:</strong> {ai_gen_summary}
      </div>
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
      <p class="text-[11px] text-slate-500 mt-2 px-1 tracking-tight">* 참고: 차트 안정성을 위해 양수 및 ESS의 충방전 총합을 순공급(Net Supply) 기준으로 환산 표기했습니다.</p>
    </div>
  </div>

  <script>
    const holidaysList = {json_holidays_js};

    // 주말 및 대한민국 공휴일 달력 빨간색 렌더링
    flatpickr("#historyDate", {{
      locale: "ko",
      dateFormat: "Y-m-d",
      defaultDate: "{target_date_dashed}",
      minDate: "2022-01-01",
      maxDate: "{latest_date_dashed}",
      onDayCreate: function(dObj, dStr, fp, dayElem) {{
        const dateStr = flatpickr.formatDate(dayElem.dateObj, "Y-m-d");
        const dayOfWeek = dayElem.dateObj.getDay();
        if (dayOfWeek === 0 || dayOfWeek === 6) {{
          dayElem.classList.add("weekend-day");
        }}
        if (holidaysList.includes(dateStr)) {{
          dayElem.classList.add("holiday-day");
        }}
      }},
      onChange: function(selectedDates, dateStr) {{
        if (!dateStr) return;
        const latest = "{latest_date_dashed}";
        if (dateStr > latest) {{
          alert("오늘 실시간 수급현황 및 SMP는 상단의 [KPX 실시간 바로가기] 버튼을 통해 확인하실 수 있습니다.\\n\\n일일 종합 분석 리포트는 24시간 마감 후 익일 아침 발행됩니다. 확정 최신일(" + latest + ")로 이동합니다.");
          window.location.href = "daily_report_{latest_date_str}.html";
          return;
        }}
        window.location.href = "daily_report_" + dateStr.replace(/-/g, '') + ".html";
      }}
    }});

    Chart.register(window['chartjs-plugin-annotation']);
    Chart.Tooltip.positioners.mouseFollow = function(elements, eventPosition) {{
      return eventPosition ? {{ x: eventPosition.x, y: eventPosition.y }} : false;
    }};
    const crosshairPlugin = {{
      id: 'crosshair',
      afterDraw: chart => {{
        if (chart.tooltip && chart.tooltip._active && chart.tooltip._active.length) {{
          const activePoint = chart.tooltip._active[0];
          const ctx = chart.ctx;
          ctx.save();
          ctx.beginPath();
          ctx.moveTo(activePoint.element.x, chart.chartArea.top);
          ctx.lineTo(activePoint.element.x, chart.chartArea.bottom);
          ctx.lineWidth = 1;
          ctx.strokeStyle = 'rgba(100, 116, 139, 0.7)';
          ctx.setLineDash([4, 4]);
          ctx.stroke();
          ctx.restore();
        }}
      }}
    }};
    Chart.register(crosshairPlugin);

    const labels = {json_hours};
    
    // 차트 레이아웃 패딩을 상단 40px, 하단 20px로 확대하여 라벨 잘림 원천 차단
    const commonOptions = {{
      responsive: true, maintainAspectRatio: false,
      layout: {{ padding: {{ top: 40, right: 20, bottom: 20, left: 10 }} }},
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ 
          labels: {{ 
            usePointStyle: true, 
            font: {{ family: 'Noto Sans KR', size: 12 }},
            generateLabels: function(chart) {{
              const original = Chart.defaults.plugins.legend.labels.generateLabels(chart);
              original.forEach(label => {{
                const ds = chart.data.datasets[label.datasetIndex];
                if (ds && ds.borderDash) {{
                  label.lineDash = ds.borderDash;
                }}
              }});
              return original;
            }}
          }} 
        }},
        tooltip: {{
          position: 'mouseFollow', backgroundColor: 'rgba(255, 255, 255, 0.95)',
          titleColor: '#001f3f', bodyColor: '#1a1a1a', borderColor: '#e5e7eb', borderWidth: 1,
          padding: 10, boxPadding: 4, usePointStyle: true,
          titleFont: {{ family: 'Noto Sans KR', size: 13, weight: 'bold' }},
          bodyFont: {{ family: 'Noto Sans KR', size: 12 }}
        }}
      }},
      scales: {{ 
        x: {{ grid: {{ display: false }} }}, 
        y: {{ 
          grid: {{ color: '#f1f5f9' }},
          grace: '20%'
        }} 
      }}
    }};

    const dynamicPeakAnnotation = {{
      type: 'box', xMin: {p_start}, xMax: {p_end},
      backgroundColor: 'rgba(217, 63, 60, 0.08)', borderWidth: 0,
      label: {{ display: true, content: '{peak_band_label} 피크', position: 'top', color: '#d93f3c', font: {{size: 11, weight: 'bold'}} }}
    }};

    // 02. SMP 차트
    new Chart(document.getElementById('smpChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [{{
          label: '시간대별 SMP (원/kWh)', data: {json.dumps(land_smp)}, pointStyle: 'line',
          borderColor: '#005587', backgroundColor: '#005587', borderWidth: 3, pointRadius: 3, pointHoverRadius: 6, tension: 0.1
        }}]
      }},
      options: {{
        ...commonOptions,
        scales: {{
          x: {{ grid: {{ display: false }} }},
          y: {{ 
            grid: {{ color: '#f1f5f9' }},
            title: {{ display: true, text: '원/kWh' }},
            grace: '20%'
          }}
        }},
        plugins: {{
          ...commonOptions.plugins,
          annotation: {{
            annotations: {{
              peakBox: dynamicPeakAnnotation,
              maxPt: {{ type: 'point', xValue: {max_smp_idx}, yValue: {max_smp}, backgroundColor: '#d93f3c', radius: 5, borderWidth: 2, borderColor: '#fff' }},
              maxLbl: {{ type: 'label', xValue: {max_smp_idx}, yValue: {max_smp}, content: ['최고 ' + Number({max_smp}).toFixed(2) + '원'], font: {{ size: 11, weight: 'bold' }}, color: '#d93f3c', yAdjust: -15 }},
              minPt: {{ type: 'point', xValue: {min_smp_idx}, yValue: {min_smp}, backgroundColor: '#005587', radius: 5, borderWidth: 2, borderColor: '#fff' }},
              minLbl: {{ type: 'label', xValue: {min_smp_idx}, yValue: {min_smp}, content: ['최저 ' + Number({min_smp}).toFixed(2) + '원'], font: {{ size: 11, weight: 'bold' }}, color: '#005587', yAdjust: -15 }}
            }}
          }}
        }}
      }}
    }});

    // 04. 발전원별 구성 차트
    new Chart(document.getElementById('generationChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ type: 'line', label: '순부하 (태양광/풍력 제외)', data: {json.dumps(g_info['net_load'])}, pointStyle: 'line', borderColor: '#1a1a1a', borderDash: [4,4], borderWidth: 2, pointRadius: 0, fill: false, z: 10, stack: 'netload_stack' }},
          {{ label: '원자력', data: {json.dumps(g_info['nuclear'])}, backgroundColor: '#f59e0b', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: '석탄', data: {json.dumps(g_info['coal'])}, backgroundColor: '#b45309', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: '유류', data: {json.dumps(g_info['oil'])}, backgroundColor: '#475569', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: 'LNG', data: {json.dumps(g_info['gas'])}, backgroundColor: '#fde047', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: '수력', data: {json.dumps(g_info['hydro'])}, backgroundColor: '#38bdf8', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: '양수발전', data: {json.dumps(g_info['pump_gen'])}, backgroundColor: '#0284c7', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: 'ESS방전', data: {json.dumps(g_info['ess_dis'])}, backgroundColor: '#1d4ed8', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: '풍력', data: {json.dumps(g_info['wind'])}, backgroundColor: '#22c55e', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: '태양광', data: {json.dumps(g_info['solar'])}, backgroundColor: '#ef4444', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: '양수펌핑(충전)', data: {json.dumps(g_info['pump_load'])}, backgroundColor: '#94a3b8', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }},
          {{ label: 'ESS충전', data: {json.dumps(g_info['ess_chg'])}, backgroundColor: '#cbd5e1', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2, stack: 'area_stack' }}
        ]
      }},
      options: {{ 
        ...commonOptions, 
        scales: {{ 
          x: {{ stacked: true, grid: {{ display: false }} }}, 
          y: {{ stacked: true, grace: '10%' }} 
        }} 
      }}
    }});

    // 05. 주요 발전원 라인 차트 (석탄 실선 통일)
    new Chart(document.getElementById('sourceLineChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: 'LNG', data: {json.dumps(g_info['gas'])}, pointStyle: 'line', borderColor: '#005587', borderWidth: 2.5, pointRadius: 0, tension: 0.3 }},
          {{ label: '석탄', data: {json.dumps(g_info['coal'])}, pointStyle: 'line', borderColor: '#d93f3c', borderWidth: 2.5, pointRadius: 0, tension: 0.3 }},
          {{ label: '신재생(태양광)', data: {json.dumps(g_info['solar'])}, pointStyle: 'line', borderColor: '#f59e0b', borderWidth: 2.5, pointRadius: 0, tension: 0.3 }}
        ]
      }},
      options: {{ ...commonOptions, plugins: {{ ...commonOptions.plugins, annotation: {{ annotations: {{ box1: dynamicPeakAnnotation }} }} }} }}
    }});

    // 06. SMP 스프레드 차트 (계통한계가격 점선 범례 적용 및 최고/최저 SMP 상시 표기)
    new Chart(document.getElementById('spreadChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ type: 'line', label: '유연성 자원 순공급 (양수/ESS)', yAxisID: 'y', data: {json.dumps(g_info['spread'])}, pointStyle: 'line', borderColor: '#059669', borderWidth: 2.5, tension: 0.3 }},
          {{ type: 'line', label: '계통한계가격(SMP)', yAxisID: 'y1', data: {json.dumps(land_smp)}, pointStyle: 'line', borderColor: '#005587', borderDash: [4,4], borderWidth: 1.5, tension: 0.3 }}
        ]
      }},
      options: {{
        ...commonOptions,
        scales: {{
          x: {{ grid: {{ display: false }} }},
          y: {{ 
            type: 'linear', 
            display: true, 
            position: 'left', 
            title: {{ display: true, text: 'MW' }},
            grace: '15%'
          }},
          y1: {{ 
            type: 'linear', 
            display: true, 
            position: 'right', 
            grid: {{ drawOnChartArea: false }}, 
            title: {{ display: true, text: '원/kWh' }},
            grace: '20%'
          }}
        }},
        plugins: {{
          ...commonOptions.plugins,
          annotation: {{
            annotations: {{
              spreadMaxPt: {{ 
                type: 'point', xScaleID: 'x', yScaleID: 'y1', 
                xValue: {max_smp_idx}, yValue: {max_smp}, 
                backgroundColor: '#d93c39', radius: 4.5, borderWidth: 2, borderColor: '#fff' 
              }},
              spreadMaxLbl: {{ 
                type: 'label', xScaleID: 'x', yScaleID: 'y1', 
                xValue: {max_smp_idx}, yValue: {max_smp}, 
                content: ['최고 ' + Number({max_smp}).toFixed(2) + '원'], 
                font: {{ size: 10, weight: 'bold' }}, color: '#d93c39', yAdjust: -14 
              }},
              spreadMinPt: {{ 
                type: 'point', xScaleID: 'x', yScaleID: 'y1', 
                xValue: {min_smp_idx}, yValue: {min_smp}, 
                backgroundColor: '#005587', radius: 4.5, borderWidth: 2, borderColor: '#fff' 
              }},
              spreadMinLbl: {{ 
                type: 'label', xScaleID: 'x', yScaleID: 'y1', 
                xValue: {min_smp_idx}, yValue: {min_smp}, 
                content: ['최저 ' + Number({min_smp}).toFixed(2) + '원'], 
                font: {{ size: 10, weight: 'bold' }}, color: '#005587', yAdjust: -14 
              }}
            }}
          }}
        }}
      }}
    }});
  </script>
</body>
</html>
"""
    filename = f"daily_report_{target_date_str}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)

print(f">> 완료: 총 {len(common_dates)}개의 리포트 HTML 파일이 생성되었습니다.")
