import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
import google.generativeai as genai

# =====================================================================
# 1. 환경 변수 및 날짜/시간 설정
# =====================================================================
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_KEY")

if not GEMINI_KEY:
    raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다. GitHub Secrets를 확인해주세요.")

genai.configure(api_key=GEMINI_KEY)

target_dt = datetime.now() - timedelta(days=1)
target_date_str = target_dt.strftime("%Y%m%d")
display_date = target_dt.strftime("%Y년 %m월 %d일")
hours = [f"{i}시" for i in range(1, 25)]

# =====================================================================
# 2. 데이터 셋 세팅 (KOGAS LNG 단가 및 수급/SMP 실적)
# =====================================================================
lng_heat_price_gcal = 105430 
lng_unit_price = round((lng_heat_price_gcal / 1000000) * 10190, 1)

forecast_demand = [50200, 48100, 46500, 46200, 48500, 53400, 59800, 62500, 61200, 59000, 56500, 60400, 64200, 66500, 68900, 71500, 74200, 73800, 72500, 69200, 66500, 63800, 59800, 55200]
actual_demand = [55100, 52800, 51400, 51600, 54500, 59400, 62100, 62800, 61500, 58800, 63400, 66800, 69500, 72800, 74900, 76500, 76817, 76400, 73500, 70200, 67800, 64100, 60100, 56200]

land_smp = [100.2, 97.5, 97.5, 97.45, 101.5, 102.4, 104.5, 109.5, 103.8, 103.5, 103.5, 103.4, 104.8, 108.5, 110.2, 183.48, 183.83, 183.74, 183.48, 183.48, 130.69, 128.5, 112.5, 105.4]

gen_nuclear = [20670]*24
gen_coal = [20541, 20120, 20340, 20850, 21200, 21500, 22100, 22500, 21500, 20100, 19364, 21500, 23400, 25500, 26466, 27116, 27404, 26500, 25643, 25471, 24800, 23500, 22100, 21000]
gen_other = [-1057, -1200, -1500, -1800, -2100, -1500, -500, 0, -1500, -3500, -4711, -2500, -682, 227, 1734, 3443, 4100, 4273, 3450, 2500, 1500, 800, -500, -800]
gen_lng = [7446, 6800, 7200, 8500, 10500, 12100, 12200, 11800, 11700, 11900, 11466, 12500, 15329, 17200, 18217, 20794, 20768, 19500, 17381, 15623, 14200, 13500, 12100, 10500]
gen_wind = [338, 350, 320, 310, 300, 310, 320, 350, 380, 390, 400, 380, 360, 350, 340, 330, 350, 380, 400, 390, 380, 370, 360, 350]
gen_solar = [0, 0, 0, 0, 0, 1200, 3500, 6500, 9500, 11200, 11906, 11500, 10162, 8407, 6079, 4548, 2500, 500, 0, 0, 0, 0, 0, 0]

gen_vre = [s + w for s, w in zip(gen_solar, gen_wind)]
net_load = [a - v for a, v in zip(actual_demand, gen_vre)]
spread_other = gen_other

recent_7days = [
    {"date": "9.16(수)", "avg": 102.81, "max": 115.44, "min": 93.77, "cap": 101398, "peak": 75206, "time": "19시", "res": 34.8},
    {"date": "9.17(목)", "avg": 104.45, "max": 161.59, "min": 90.09, "cap": 102056, "peak": 75670, "time": "19시", "res": 34.9},
    {"date": "9.18(금)", "avg": 103.56, "max": 117.08, "min": 95.33, "cap": 102286, "peak": 76071, "time": "17시", "res": 34.5},
    {"date": "9.19(토)", "avg": 105.40, "max": 120.29, "min": 95.51, "cap": 100537, "peak": 75330, "time": "19시", "res": 33.5},
    {"date": "9.20(일)", "avg": 103.67, "max": 111.66, "min": 93.02, "cap": 94536, "peak": 67651, "time": "19시", "res": 39.7},
    {"date": "9.21(월)", "avg": 98.83, "max": 111.93, "min": 6.31, "cap": 94154, "peak": 66528, "time": "20시", "res": 41.5},
    {"date": "9.22(화)", "avg": 126.08, "max": 183.83, "min": 97.45, "cap": 98874, "peak": 76817, "time": "19시", "res": 28.7}
]
reversed_7days = list(reversed(recent_7days))

avg_smp = round(sum(land_smp) / len(land_smp), 2)
max_smp = max(land_smp)
min_smp = min(land_smp)

max_peak_actual = max(actual_demand)
peak_hour_str = hours[actual_demand.index(max_peak_actual)]
reserve_ratio = recent_7days[-1]['res']

prev_avg_smp = recent_7days[-2]['avg']
diff_avg_smp = round(avg_smp - prev_avg_smp, 2)
diff_max_smp = round(max_smp - recent_7days[-2]['max'], 2)

# =====================================================================
# 3. Gemini AI 보고서 요약
# =====================================================================
prompt_main = f"""
당신은 글로벌 전략 컨설팅 펌(McKinsey)의 에너지 파트너입니다.
아래 데이터를 바탕으로 C-level 임원을 위한 '일일 전력시장 요약'을 작성하세요.

[데이터]
- 기준일: {display_date}
- 최고 SMP: {max_smp} 원/kWh (최저 {min_smp} 원/kWh)
- 최대부하: {max_peak_actual:,} MW ({peak_hour_str})
- 특이사항: 전일 대비 가중평균 SMP {diff_avg_smp:+}원 변동. 최고 SMP 및 최대부하가 17~21시 구간에 동시 관찰됨.

[작성 가이드]
1. 팩트(Fact) 기반으로 객관적 현상을 우선 서술하며, 단 하루 실적만으로 구조적 변화를 과도하게 단정짓지 말 것.
2. 평소 패턴과 다른 급변동이나 특이 수치(Outlier)가 있을 경우에만 신중하게 분석 의견 및 시사점을 추가할 것.
3. 반드시 모든 문장은 명사형/단어 종결형(개조식)으로 끝낼 것 (예: ~관찰됨, ~수준임, ~판단됨).
4. 출력 형식은 HTML <ul> 및 <li> 태그만 사용.
"""

prompt_chart4 = f"""
당신은 전력시장 분석가입니다.
금일 '발전원별 수급 구성 및 순부하(Net Load)' 현황에 대한 의견을 1~2문장의 개조식(명사 종결형)으로 작성하세요.
- 데이터: 신재생(태양광+풍력) 최대 {max(gen_vre)}MW, 순부하 최저 {min(net_load)}MW
- 가이드: 팩트 위주의 분석(예: 주간 발전량 증가로 순부하 하락함 등)을 HTML 없이 순수 텍스트로만 출력.
"""

try:
    model = genai.GenerativeModel("gemini-2.0-flash")
    res_main = model.generate_content(prompt_main)
    ai_summary = res_main.text.replace('```html', '').replace('```', '').strip()
    
    res_chart4 = model.generate_content(prompt_chart4)
    ai_gen_summary = res_chart4.text.replace('```html', '').replace('```', '').strip()
except Exception as e:
    print(f"Gemini API 호출 중 오류 발생: {e}")
    ai_summary = f"""
    <ul>
      <li><strong>수급 및 가격 지표:</strong> 최대전력수요 {max_peak_actual:,}MW({peak_hour_str}) 및 최고 SMP {max_smp}원/kWh가 저녁 17~21시 구간에 집중적으로 관찰됨.</li>
      <li><strong>팩트 및 특이동향:</strong> 전일 대비 가중평균 SMP가 {diff_avg_smp:+}원 변동하였으며, 주간 신재생 유입에 따른 순부하 하락 패턴이 지속 관찰되고 있음. (단기 실적이므로 구조적 변화 여부는 모니터링 요망)</li>
      <li><strong>시사점:</strong> 저녁 피크 램핑(Ramping) 수요 급증에 대비하여, 가스 복합발전 및 유연성 자원(ESS, 양수)의 입찰 전략 고도화가 요구됨.</li>
    </ul>
    """
    ai_gen_summary = "주간 신재생 발전량 증가로 낮 시간대 순부하(Net Load)가 최저점을 기록하였으며, 일몰 후 감소분을 LNG 복합 및 양수 발전이 안정적으로 대체함."

# =====================================================================
# 4. HTML 테이블 사전 생성 (f-string 중첩 오류 방지용 분리)
# =====================================================================
table_rows_html = ""
for i, row in enumerate(reversed_7days):
    bg_class = "bg-slate-50 font-bold" if i == 0 else ""
    text_class = "text-holiday" if '토' in row['date'] or '일' in row['date'] else ""
    
    table_rows_html += f"""
    <tr class="{bg_class}">
        <td class="{text_class}">{row['date']}</td>
        <td>{row['avg']:.2f}</td>
        <td>{row['max']:.2f}</td>
        <td>{row['min']:.2f}</td>
        <td>{row['cap']:,}</td>
        <td>{row['peak']:,}</td>
        <td>{row['time']}</td>
        <td>{row['res']}%</td>
    </tr>
    """

diff_avg_color = "text-rose-600" if diff_avg_smp > 0 else "text-blue-600"
diff_max_color = "text-rose-600" if diff_max_smp > 0 else "text-blue-600"

# JSON 덤프 데이터 사전 변수화 (JavaScript 영역 내 f-string 오류 원천 차단)
json_hours = json.dumps(hours)
json_land_smp = json.dumps(land_smp)
json_net_load = json.dumps(net_load)
json_gen_nuclear = json.dumps(gen_nuclear)
json_gen_coal = json.dumps(gen_coal)
json_gen_other = json.dumps(gen_other)
json_gen_lng = json.dumps(gen_lng)
json_gen_wind = json.dumps(gen_wind)
json_gen_solar = json.dumps(gen_solar)
json_spread_other = json.dumps(spread_other)
json_actual_demand = json.dumps(actual_demand)
json_forecast_demand = json.dumps(forecast_demand)
json_demand_diff = json.dumps([a - f for a, f in zip(actual_demand, forecast_demand)])

# =====================================================================
# 5. HTML 렌더링
# =====================================================================
html_template = f"""<!DOCTYPE html>
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
    .chart-container {{ position: relative; height: 350px; width: 100%; border: 1px solid #e5e7eb; padding: 1rem; background-color: #ffffff; }}
    table th, table td {{ border: 1px solid #e5e7eb; padding: 12px; text-align: center; font-size: 0.875rem; }}
    table th {{ background-color: #f8fafc; font-weight: 700; color: #334155; }}
    .text-holiday {{ color: #dc2626; font-weight: 700; }}
    h2 {{ font-size: 1.125rem; font-weight: 700; color: #001f3f; margin-bottom: 0.75rem; border-bottom: 2px solid #e5e7eb; padding-bottom: 0.5rem; }}
    .summary-box li {{ margin-bottom: 0.5rem; }}
  </style>
</head>
<body class="p-4 md:p-8">
  <div class="max-w-6xl mx-auto space-y-8">

    <!-- Header -->
    <div class="mckinsey-border pt-4 pb-2 flex justify-between items-end">
      <div>
        <h1 class="text-3xl font-black text-slate-900 tracking-tight">전력시장 일일 요약 (KPX)</h1>
        <p class="text-sm text-slate-500 mt-1">기준일: {display_date} | 육지 기준</p>
      </div>
    </div>

    <!-- Executive Summary -->
    <div>
      <h2>00. Executive Summary</h2>
      <div class="bg-slate-50 border border-slate-200 p-6 text-base md:text-lg text-slate-800 leading-relaxed font-medium summary-box">
        {ai_summary}
      </div>
    </div>

    <!-- 1. KPI Indicators -->
    <div>
      <h2>01. 전일 전력시장 실적요약</h2>
      <div class="grid grid-cols-2 md:grid-cols-7 gap-0 border border-slate-200">
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center bg-slate-50">
          <p class="text-xs font-bold text-slate-500 mb-1">LNG 단가 (당월)</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-700">{lng_unit_price:,}</span><span class="text-[11px] font-medium pb-1">원/Nm³</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 text-slate-400 tracking-tighter">10,190kcal 환산</p>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">가중평균 SMP</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-900">{avg_smp}</span><span class="text-xs font-medium pb-1">원</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 {diff_avg_color}">전일비 {diff_avg_smp:+}원</p>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center bg-rose-50/30">
          <p class="text-xs font-bold text-slate-500 mb-1">최고 SMP</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-[#d93f3c]">{max_smp}</span><span class="text-xs font-medium pb-1">원</span>
          </div>
          <p class="text-[11px] font-semibold mt-1 {diff_max_color}">전일비 {diff_max_smp:+}원</p>
        </div>
        <div class="p-4 border-b md:border-b-0 md:border-r border-slate-200 flex flex-col justify-center">
          <p class="text-xs font-bold text-slate-500 mb-1">최저 SMP</p>
          <div class="flex items-end gap-1">
            <span class="text-2xl font-black text-slate-900">{min_smp}</span><span class="text-xs font-medium pb-1">원</span>
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
            <span class="text-2xl font-black text-emerald-600">{reserve_ratio}</span><span class="text-xs font-medium pb-1">%</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 2. 시간대별 SMP 차트 -->
    <div>
      <h2>02. 시간대별 계통한계가격(SMP) 및 피크 밴드</h2>
      <div class="chart-container">
        <canvas id="smpChart"></canvas>
      </div>
    </div>

    <!-- 3. 최근 7일 테이블 (내림차순) -->
    <div>
      <h2>03. 최근 7일 전력수급 및 SMP 실적</h2>
      <div class="overflow-x-auto">
        <table class="w-full border-collapse">
          <thead>
            <tr>
              <th rowspan="2">구분</th>
              <th colspan="3">SMP (원/kWh)</th>
              <th colspan="4">전력수급 실적 (MW)</th>
            </tr>
            <tr>
              <th>가중평균</th>
              <th>최고</th>
              <th>최저</th>
              <th>공급능력</th>
              <th>최대전력</th>
              <th>피크</th>
              <th>예비율</th>
            </tr>
          </thead>
          <tbody>
            {table_rows_html}
          </tbody>
        </table>
      </div>
    </div>

    <!-- 4. 발전원별 실시간 수급 현황 -->
    <div>
      <h2>04. 발전원별 실시간 수급 구성 및 순부하</h2>
      <div class="chart-container">
        <canvas id="generationChart"></canvas>
      </div>
      <div class="mt-3 bg-slate-50 border border-slate-200 p-4 rounded text-sm text-slate-800 font-medium">
        💡 <strong>분석 의견:</strong> {ai_gen_summary}
      </div>
    </div>

    <!-- 5. 주요 발전원 상세 추이 -->
    <div>
      <h2>05. 주요 발전원 시간대별 출력 추이 (SMP 피크 밴드 중첩)</h2>
      <div class="chart-container">
        <canvas id="sourceLineChart"></canvas>
      </div>
    </div>

    <!-- 6. 양수·ESS 스프레드 -->
    <div>
      <h2>06. SMP 스프레드</h2>
      <div class="chart-container">
        <canvas id="spreadChart"></canvas>
      </div>
      <p class="text-[11px] text-slate-500 mt-2 px-1 tracking-tight">* 참고: 원천 데이터 집계 기준상 양수·ESS·유류가 '기타' 항목으로 합산되어 개별 구분이 불가하므로, 위 유연성 자원 스프레드는 세 자원의 혼합분을 기준으로 작성되었습니다.</p>
    </div>

    <!-- 7. 수요예측 대비 실적 -->
    <div>
      <h2>07. 하루전 수요예측 vs 전력시장 수요 실적</h2>
      <div class="chart-container">
        <canvas id="demandDiffChart"></canvas>
      </div>
    </div>

  </div>

  <script>
    Chart.register(window['chartjs-plugin-annotation']);

    Chart.Tooltip.positioners.mouseFollow = function(elements, eventPosition) {{
      if (eventPosition) {{
        return {{ x: eventPosition.x, y: eventPosition.y }};
      }}
      return false;
    }};

    const crosshairPlugin = {{
      id: 'crosshair',
      afterDraw: chart => {{
        if (chart.tooltip && chart.tooltip._active && chart.tooltip._active.length) {{
          const activePoint = chart.tooltip._active[0];
          const ctx = chart.ctx;
          const x = activePoint.element.x;
          const topY = chart.chartArea.top;
          const bottomY = chart.chartArea.bottom;
          ctx.save();
          ctx.beginPath();
          ctx.moveTo(x, topY);
          ctx.lineTo(x, bottomY);
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
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{
          labels: {{
            usePointStyle: true,
            font: {{ family: 'Noto Sans KR', size: 12 }}
          }}
        }},
        tooltip: {{
          position: 'mouseFollow',
          backgroundColor: 'rgba(255, 255, 255, 0.95)',
          titleColor: '#001f3f',
          bodyColor: '#1a1a1a',
          borderColor: '#e5e7eb',
          borderWidth: 1,
          padding: 10,
          boxPadding: 4,
          usePointStyle: true,
          titleFont: {{ family: 'Noto Sans KR', size: 13, weight: 'bold' }},
          bodyFont: {{ family: 'Noto Sans KR', size: 12 }}
        }}
      }},
      scales: {{
        x: {{ grid: {{ display: false }} }},
        y: {{ grid: {{ color: '#f1f5f9' }} }}
      }}
    }};

    const peakBandAnnotation = {{
      type: 'box',
      xMin: 16, xMax: 20,
      backgroundColor: 'rgba(217, 63, 60, 0.08)',
      borderWidth: 0,
      label: {{ display: true, content: '피크 구간 (17~21시)', position: 'top', color: '#d93f3c', font: {{size: 11, weight: 'bold'}} }}
    }};

    new Chart(document.getElementById('smpChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [{{
          label: '시간대별 SMP (원/kWh)', data: {json_land_smp}, pointStyle: 'line',
          borderColor: '#005587', backgroundColor: '#005587', borderWidth: 3, pointRadius: 4, pointHoverRadius: 6, tension: 0.1
        }}]
      }},
      options: {{ ...commonOptions, plugins: {{ ...commonOptions.plugins, annotation: {{ annotations: {{ box1: peakBandAnnotation }} }} }} }}
    }});

    new Chart(document.getElementById('generationChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ type: 'line', label: '순부하 (태양광/풍력 제외)', data: {json_net_load}, pointStyle: 'line', borderColor: '#1a1a1a', borderDash: [4,4], borderWidth: 2, pointRadius: 0, fill: false, z: 10 }},
          {{ label: '원자력', data: {json_gen_nuclear}, backgroundColor: '#4f46e5', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2 }},
          {{ label: '석탄', data: {json_gen_coal}, backgroundColor: '#ea580c', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2 }},
          {{ label: '기타/수력', data: {json_gen_other}, backgroundColor: '#059669', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2 }},
          {{ label: '가스(LNG)', data: {json_gen_lng}, backgroundColor: '#3b82f6', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2 }},
          {{ label: '풍력', data: {json_gen_wind}, backgroundColor: '#0ea5e9', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2 }},
          {{ label: '태양광', data: {json_gen_solar}, backgroundColor: '#f59e0b', borderColor: 'transparent', fill: true, pointRadius: 0, tension: 0.2 }}
        ]
      }},
      options: {{ ...commonOptions, scales: {{ x: {{ stacked: true, grid: {{ display: false }} }}, y: {{ stacked: true }} }} }}
    }});

    new Chart(document.getElementById('sourceLineChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: '가스 (LNG)', data: {json_gen_lng}, pointStyle: 'line', borderColor: '#005587', borderWidth: 2.5, pointRadius: 0, tension: 0.3 }},
          {{ label: '석탄', data: {json_gen_coal}, pointStyle: 'line', borderColor: '#d93f3c', borderDash: [5,5], borderWidth: 2, pointRadius: 0, tension: 0.3 }},
          {{ label: '신재생(태양광)', data: {json_gen_solar}, pointStyle: 'line', borderColor: '#f59e0b', borderWidth: 2, pointRadius: 0, tension: 0.3 }}
        ]
      }},
      options: {{ ...commonOptions, plugins: {{ ...commonOptions.plugins, annotation: {{ annotations: {{ box1: peakBandAnnotation }} }} }} }}
    }});

    new Chart(document.getElementById('spreadChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ type: 'line', label: '유연성 자원 (양수/ESS/유류 순공급)', yAxisID: 'y', data: {json_spread_other}, pointStyle: 'line', borderColor: '#059669', borderWidth: 2.5, tension: 0.3 }},
          {{ type: 'line', label: '계통한계가격(SMP)', yAxisID: 'y1', data: {json_land_smp}, pointStyle: 'line', borderColor: '#005587', borderDash: [4,4], borderWidth: 1.5, tension: 0.3 }}
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

    new Chart(document.getElementById('demandDiffChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ type: 'bar', label: '실적-예측 오차(MW)', yAxisID: 'y1', data: {json_demand_diff}, pointStyle: 'rect', backgroundColor: 'rgba(59, 130, 246, 0.4)' }},
          {{ type: 'line', label: '하루전 수요예측', yAxisID: 'y', data: {json_forecast_demand}, pointStyle: 'line', borderColor: '#005587', borderDash: [5,5], borderWidth: 2, pointRadius: 0, tension: 0.3 }},
          {{ type: 'line', label: '전력시장 수요실적', yAxisID: 'y', data: {json_actual_demand}, pointStyle: 'line', borderColor: '#d93f3c', borderWidth: 2.5, pointRadius: 0, tension: 0.3 }}
        ]
      }},
      options: {{
        ...commonOptions,
        scales: {{
          x: {{ grid: {{ display: false }} }},
          y: {{ type: 'linear', display: true, position: 'left' }},
          y1: {{ type: 'linear', display: true, position: 'right', grid: {{ drawOnChartArea: false }}, min: 0, max: 10000 }}
        }}
      }}
    }});
  </script>
</body>
</html>
"""

# =====================================================================
# 6. 파일 저장
# =====================================================================
filename = f"daily_report_{target_date_str}.html"
with open(filename, "w", encoding="utf-8") as f:
    f.write(html_template)

print(f"매킨지 스타일 보고서 생성 완료: {filename}")
