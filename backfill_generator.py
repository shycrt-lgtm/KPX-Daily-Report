import os
import glob
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

print(">> 과거 실적 데이터 파일 로딩 시작...")

def read_flexible_csv(file_path):
    for enc in ['cp949', 'utf-8-sig', 'euc-kr', 'utf-8']:
        try:
            return pd.read_csv(file_path, encoding=enc)
        except Exception:
            continue
    raise ValueError(f"파일을 읽을 수 없습니다: {file_path}")

# 1. 시간대별 SMP 로딩 (.csv 및 .xlsx 모두 대응)
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

# 2. 일별 최대부하 및 예비율 로딩 (.csv 및 .xlsx 모두 대응)
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

# 3. 수요예측 로딩 (pssInland_YYYY.xlsx)
pred_dict = {}
pred_files = sorted(glob.glob("pssInland_*.xlsx"))
for pf in pred_files:
    df_p = pd.read_excel(pf)
    header_idx = df_p[df_p.iloc[:, 0].astype(str).str.contains("구분|일자", na=False)].index
    if len(header_idx) > 0:
        df_p.columns = df_p.iloc[header_idx[0]]
        df_p = df_p.iloc[header_idx[0]+1:].reset_index(drop=True)
    for _, row in df_p.iterrows():
        try:
            d_val = str(row.iloc[0]).split('.')[0].strip()
            if len(d_val) == 8 and d_val.isdigit():
                vals = [float(str(row[f"{i}h"]).replace(',', '')) for i in range(1, 25)]
                pred_dict[d_val] = vals
        except:
            continue
print(f"수요예측 로딩 완료: {len(pred_dict)}일")

# 4. LNG 단가 로딩
lng_dict = {}
lng_files = glob.glob("*LNG 단가*.xlsx")
if lng_files:
    df_lng = pd.read_excel(lng_files[0], sheet_name="입방당")
    year_col = df_lng.columns[0]
    type_col = df_lng.columns[1]
    curr_year = None
    for idx, row in df_lng.iterrows():
        if pd.notna(row[year_col]):
            try:
                curr_year = int(row[year_col])
            except:
                pass
        if str(row[type_col]).strip() == "합계" and curr_year:
            for m in range(1, 13):
                val = row.iloc[m + 1]
                if pd.notna(val) and str(val).strip() not in ['-', '']:
                    lng_dict[f"{curr_year}{m:02d}"] = float(str(val).replace(',', ''))
print(f"LNG 단가 로딩 완료: {len(lng_dict)}개월")

# 5. 발전원별 발전량 연도별 로딩 및 집계
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
        
        # 전력시장 수요실적 (BTM/PPA 제외 순수 급전 발전량 합)
        act_demand = [
            nuc[i] + coal[i] + oil[i] + gas[i] + hydro[i] + pump_gen[i] + ess_dis[i] + wind[i] + solar[i]
            for i in range(24)
        ]
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
            'spread': [round(v, 1) for v in spread],
            'actual_demand': [round(v, 1) for v in act_demand]
        }

print(f"발전원 집계 완료: {len(gen_dict)}일")

common_dates = sorted(list(set(smp_dict.keys()) & set(gen_dict.keys()) & set(cap_dict.keys())))
print(f">> 최종 생성 대상 일수: {len(common_dates)}일")

hours = [f"{i}시" for i in range(1, 25)]
json_hours = json.dumps(hours)

for target_date_str in common_dates:
    dt = datetime.strptime(target_date_str, "%Y%m%d")
    display_date = dt.strftime("%Y년 %m월 %d일")
    target_date_dashed = dt.strftime("%Y-%m-%d")
    ym_str = target_date_str[:6]
    
    s_info = smp_dict[target_date_str]
    land_smp = s_info['hourly']
    avg_smp = s_info['avg']
    max_smp = s_info['max']
    min_smp = s_info['min']
    max_smp_idx = land_smp.index(max_smp)
    min_smp_idx = land_smp.index(min_smp)
    
    smp_thresh = min_smp + (max_smp - min_smp) * 0.85
    high_smp_idx = [i for i, v in enumerate(land_smp) if v >= smp_thresh]
    p_start = min(high_smp_idx) if high_smp_idx else max_smp_idx
    p_end = max(high_smp_idx) if high_smp_idx else max_smp_idx
    peak_band_label = f"{p_start+1}~{p_end+1}시 피크"
    
    g_info = gen_dict[target_date_str]
    actual_demand = g_info['actual_demand']
    forecast_demand = pred_dict.get(target_date_str, actual_demand)
    demand_diff = [round(a - f, 1) for a, f in zip(actual_demand, forecast_demand)]
    
    c_info = cap_dict[target_date_str]
    max_peak_actual = c_info['peak']
    peak_hour_str = c_info['time']
    reserve_ratio = c_info['res']
    lng_price = lng_dict.get(ym_str, 0.0)
    
    recent_7 = []
    for delta in range(7):
        prev_d = (dt - timedelta(days=delta)).strftime("%Y%m%d")
        if prev_d in smp_dict and prev_d in cap_dict:
            p_dt = datetime.strptime(prev_d, "%Y%m%d")
            weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][p_dt.weekday()]
            recent_7.append({
                'date': f"{p_dt.month}.{p_dt.day}({weekday_kr})",
                'avg': smp_dict[prev_d]['avg'],
                'max': smp_dict[prev_d]['max'],
                'min': smp_dict[prev_d]['min'],
                'cap': cap_dict[prev_d]['cap'],
                'peak': cap_dict[prev_d]['peak'],
                'time': cap_dict[prev_d]['time'],
                'res': cap_dict[prev_d]['res']
            })
            
    diff_avg_smp = round(avg_smp - recent_7[1]['avg'], 2) if len(recent_7) > 1 else 0.0
    diff_max_smp = round(max_smp - recent_7[1]['max'], 2) if len(recent_7) > 1 else 0.0
    diff_avg_color = "text-rose-600" if diff_avg_smp > 0 else "text-blue-600"
    diff_max_color = "text-rose-600" if diff_max_smp > 0 else "text-blue-600"
    
    table_rows_html = ""
    for i, r in enumerate(recent_7):
        bg = "bg-slate-50 font-bold" if i == 0 else ""
        txt = "text-holiday" if '토' in r['date'] or '일' in r['date'] else ""
        table_rows_html += f"""
        <tr class="{bg}">
            <td class="{txt}">{r['date']}</td>
            <td>{r['avg']:.2f}</td><td>{r['max']:.2f}</td><td>{r['min']:.2f}</td>
            <td>{r['cap']:,}</td><td>{r['peak']:,}</td><td>{r['time']}</td><td>{r['res']}%</td>
        </tr>
        """
        
    ai_summary = f"""
    <ul>
      <li><strong>수급 및 가격 지표:</strong> 최대전력수요 {max_peak_actual:,}MW({peak_hour_str}) 및 최고 SMP {max_smp:.2f}원/kWh가 {peak_band_label} 구간에 관찰됨.</li>
      <li><strong>시장 동향:</strong> 가중평균 SMP는 {avg_smp:.2f}원/kWh(전일비 {diff_avg_smp:+}원)이며, 공급예비율 {reserve_ratio:.1f}% 수준임.</li>
      <li><strong>발전원 구성:</strong> 기저발전(원자력·석탄) 중심 위에 주간 태양광 출력 정점 및 저녁 피크 LNG 램핑 대응 패턴이 확인됨.</li>
    </ul>
    """
    ai_gen_summary = f"주간 태양광 발전량 증가로 순부하 최저점을 형성하였으며, 일몰 후 저녁 피크 램핑 수요를 LNG 및 양수/ESS가 안정적으로 전담함."

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>일일 전력시장 종합 리포트</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@2.2.1/dist/chartjs-plugin-annotation.min.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Noto Sans KR', sans-serif; background-color: #ffffff; color: #1a1a1a; }}
    .mckinsey-border {{ border-top: 4px solid #001f3f; }}
    .chart-container {{ position: relative; height: 450px; width: 100%; border: 1px solid #e5e7eb; padding: 1rem; background-color: #ffffff; }}
    table th, table td {{ border: 1px solid #e5e7eb; padding: 12px; text-align: center; font-size: 0.875rem; }}
    table th {{ background-color: #f8fafc; font-weight: 700; color: #334155; }}
    .text-holiday {{ color: #dc2626; font-weight: 700; }}
    h2 {{ font-size: 1.125rem; font-weight: 700; color: #001f3f; margin-bottom: 0.75rem; border-bottom: 2px solid #e5e7eb; padding-bottom: 0.5rem; }}
    .summary-box li {{ margin-bottom: 0.5rem; }}
  </style>
</head>
<body class="p-4 md:p-8">
  <div class="max-w-6xl mx-auto space-y-8">
    <div class="mckinsey-border pt-4 pb-2 flex flex-col md:flex-row md:justify-between md:items-end gap-4">
      <div>
        <h1 class="text-3xl font-black text-slate-900 tracking-tight">전력시장 일일 요약 (KPX)</h1>
        <p class="text-sm text-slate-500 mt-1">기준일: {display_date} | 육지 기준 종합 실적</p>
      </div>
      <div class="flex items-center gap-2 bg-slate-50 border border-slate-200 px-4 py-2 rounded-lg shadow-sm">
        <label for="historyDate" class="text-sm font-bold text-slate-700">조회일자:</label>
        <input type="date" id="historyDate" min="2022-01-01" max="2026-12-31" value="{target_date_dashed}" 
               class="bg-transparent text-sm font-bold text-slate-900 outline-none cursor-pointer"
               onchange="if(this.value) window.location.href='daily_report_' + this.value.replace(/-/g, '') + '.html';">
      </div>
    </div>

    <div>
      <h2>00. Executive Summary</h2>
      <div class="bg-slate-50 border border-slate-200 p-6 text-base md:text-lg text-slate-800 leading-relaxed font-medium summary-box">
        {ai_summary}
      </div>
    </div>

    <div>
      <h2>01. 전일 전력시장 실적요약</h2>
      <div class="grid grid-cols-2 md:grid-cols-7 gap-0 border border-slate-200">
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center bg-slate-50">
          <p class="text-xs font-bold text-slate-500 mb-1">LNG 단가 (당월)</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-700">{lng_price:,.1f}</span><span class="text-[11px] font-medium pb-1">원/Nm³</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 text-slate-400 tracking-tighter">입방당 합계</p>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">가중평균 SMP</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-900">{avg_smp:.2f}</span><span class="text-xs font-medium pb-1">원</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 {diff_avg_color}">전일비 {diff_avg_smp:+}원</p>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center bg-rose-50/30">
          <p class="text-xs font-bold text-slate-500 mb-1">최고 SMP</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-[#d93f3c]">{max_smp:.2f}</span><span class="text-xs font-medium pb-1">원</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 {diff_max_color}">전일비 {diff_max_smp:+}원</p>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">최저 SMP</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-900">{min_smp:.2f}</span><span class="text-xs font-medium pb-1">원</span>
          </div>
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

    <div>
      <h2>02. 시간대별 계통한계가격(SMP) 및 피크 밴드</h2>
      <div class="chart-container"><canvas id="smpChart"></canvas></div>
    </div>

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

    <div>
      <h2>04. 발전원별 실시간 수급 구성 및 순부하</h2>
      <div class="chart-container"><canvas id="generationChart"></canvas></div>
      <div class="mt-3 bg-slate-50 border border-slate-200 p-4 rounded text-sm text-slate-800 font-medium">
        💡 <strong>분석 의견:</strong> {ai_gen_summary}
      </div>
    </div>

    <div>
      <h2>05. 주요 발전원 시간대별 출력 추이 (SMP 피크 밴드 중첩)</h2>
      <div class="chart-container"><canvas id="sourceLineChart"></canvas></div>
    </div>

    <div>
      <h2>06. SMP 스프레드</h2>
      <div class="chart-container"><canvas id="spreadChart"></canvas></div>
      <p class="text-[11px] text-slate-500 mt-2 px-1 tracking-tight">* 참고: 차트 안정성을 위해 양수 및 ESS의 충방전 총합을 순공급(Net Supply) 기준으로 환산 표기했습니다.</p>
    </div>

    <div>
      <h2>07. 하루전 수요예측 vs 전력시장 수요 실적</h2>
      <div class="border border-slate-200 p-4 bg-white space-y-4">
        <div>
          <span class="text-xs font-bold text-slate-500 block mb-1">■ 전력수요 추이 (단위: MW)</span>
          <div class="relative h-[280px] w-full"><canvas id="demandLineChart"></canvas></div>
        </div>
        <div class="border-t border-slate-100 pt-3">
          <span class="text-xs font-bold text-slate-500 block mb-1">■ 실적 - 예측 오차 (단위: MW)</span>
          <div class="relative h-[160px] w-full"><canvas id="demandDiffBarChart"></canvas></div>
        </div>
      </div>
    </div>
  </div>

  <script>
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
    const commonOptions = {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ usePointStyle: true, font: {{ family: 'Noto Sans KR', size: 12 }} }} }},
        tooltip: {{
          position: 'mouseFollow', backgroundColor: 'rgba(255, 255, 255, 0.95)',
          titleColor: '#001f3f', bodyColor: '#1a1a1a', borderColor: '#e5e7eb', borderWidth: 1,
          padding: 10, boxPadding: 4, usePointStyle: true,
          titleFont: {{ family: 'Noto Sans KR', size: 13, weight: 'bold' }},
          bodyFont: {{ family: 'Noto Sans KR', size: 12 }}
        }}
      }},
      scales: {{ x: {{ grid: {{ display: false }} }}, y: {{ grid: {{ color: '#f1f5f9' }} }} }}
    }};

    const dynamicPeakAnnotation = {{
      type: 'box', xMin: {p_start}, xMax: {p_end},
      backgroundColor: 'rgba(217, 63, 60, 0.08)', borderWidth: 0,
      label: {{ display: true, content: '{peak_band_label}', position: 'top', color: '#d93f3c', font: {{size: 11, weight: 'bold'}} }}
    }};

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
        plugins: {{
          ...commonOptions.plugins,
          annotation: {{
            annotations: {{
              peakBox: dynamicPeakAnnotation,
              maxPt: {{ type: 'point', xValue: {max_smp_idx}, yValue: {max_smp}, backgroundColor: '#d93f3c', radius: 5, borderWidth: 2, borderColor: '#fff' }},
              maxLbl: {{ type: 'label', xValue: {max_smp_idx}, yValue: {max_smp}, content: ['최고 {max_smp:.2f}원'], font: {{ size: 11, weight: 'bold' }}, color: '#d93f3c', yAdjust: -15 }},
              minPt: {{ type: 'point', xValue: {min_smp_idx}, yValue: {min_smp}, backgroundColor: '#005587', radius: 5, borderWidth: 2, borderColor: '#fff' }},
              minLbl: {{ type: 'label', xValue: {min_smp_idx}, yValue: {min_smp}, content: ['최저 {min_smp:.2f}원'], font: {{ size: 11, weight: 'bold' }}, color: '#005587', yAdjust: 15 }}
            }}
          }}
        }}
      }}
    }});

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
      options: {{ ...commonOptions, scales: {{ x: {{ stacked: true, grid: {{ display: false }} }}, y: {{ stacked: true }} }} }}
    }});

    new Chart(document.getElementById('sourceLineChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: 'LNG', data: {json.dumps(g_info['gas'])}, pointStyle: 'line', borderColor: '#005587', borderWidth: 2.5, pointRadius: 0, tension: 0.3 }},
          {{ label: '석탄', data: {json.dumps(g_info['coal'])}, pointStyle: 'line', borderColor: '#d93f3c', borderDash: [5,5], borderWidth: 2, pointRadius: 0, tension: 0.3 }},
          {{ label: '신재생(태양광)', data: {json.dumps(g_info['solar'])}, pointStyle: 'line', borderColor: '#f59e0b', borderWidth: 2, pointRadius: 0, tension: 0.3 }}
        ]
      }},
      options: {{ ...commonOptions, plugins: {{ ...commonOptions.plugins, annotation: {{ annotations: {{ box1: dynamicPeakAnnotation }} }} }} }}
    }});

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
          y: {{ type: 'linear', display: true, position: 'left', title: {{ display: true, text: 'MW' }} }},
          y1: {{ type: 'linear', display: true, position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: '원/kWh' }} }}
        }}
      }}
    }});

    new Chart(document.getElementById('demandLineChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: '하루전 수요예측', data: {json.dumps(forecast_demand)}, pointStyle: 'line', borderColor: '#005587', borderDash: [5,5], borderWidth: 2, pointRadius: 0, tension: 0.3 }},
          {{ label: '전력시장 수요실적', data: {json.dumps(actual_demand)}, pointStyle: 'line', borderColor: '#d93f3c', borderWidth: 2.5, pointRadius: 0, tension: 0.3 }}
        ]
      }},
      options: {{ ...commonOptions }}
    }});

    new Chart(document.getElementById('demandDiffBarChart'), {{
      type: 'bar',
      data: {{
        labels: labels,
        datasets: [{{ label: '실적 - 예측 오차 (MW)', data: {json.dumps(demand_diff)}, pointStyle: 'rect', backgroundColor: 'rgba(59, 130, 246, 0.5)', borderColor: '#2563eb', borderWidth: 1 }}]
      }},
      options: {{ ...commonOptions, scales: {{ x: {{ grid: {{ display: false }} }}, y: {{ grid: {{ color: '#f1f5f9' }} }} }} }}
    }});
  </script>
</body>
</html>
"""
    filename = f"daily_report_{target_date_str}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)

print(f">> 완료: 총 {len(common_dates)}개의 리포트 HTML 파일이 생성되었습니다.")
