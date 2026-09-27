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
target_date_dashed = target_dt.strftime("%Y-%m-%d")  # 달력 UI용 포맷
hours = [f"{i}시" for i in range(1, 25)]

# =====================================================================
# 2. 데이터 셋 세팅 (KOGAS LNG 단가 및 수급/SMP 실적)
# =====================================================================
lng_heat_price_gcal = 105430 
lng_unit_price = round((lng_heat_price_gcal / 1000000) * 10190, 1)

forecast_demand = [50200, 48100, 46500, 46200, 48500, 53400, 59800, 62500, 61200, 59000, 56500, 60400, 64200, 66500, 68900, 71500, 74200, 73800, 72500, 69200, 66500, 63800, 59800, 55200]
actual_demand = [55100, 52800, 51400, 51600, 54500, 59400, 62100, 62800, 61500, 58800, 63400, 66800, 69500, 72800, 74900, 76500, 76817, 76400, 73500, 70200, 67800, 64100, 60100, 56200]
land_smp = [100.2, 97.5, 97.5, 97.45, 101.5, 102.4, 104.5, 109.5, 103.8, 103.5, 103.5, 103.4, 104.8, 108.5, 110.2, 183.48, 183.83, 183.74, 183.48, 183.48, 130.69, 128.5, 112.5, 105.4]

gen_nuclear = [20700]*24
gen_coal = [22900, 22500, 21800, 21500, 21500, 22000, 22800, 23500, 23100, 22500, 22100, 22500, 23500, 24800, 26000, 27500, 28100, 27500, 26500, 25500, 25000, 24500, 24000, 23500]
gen_oil = [160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160, 160]
gen_gas = [11500, 10500, 9500, 9800, 10500, 12500, 14500, 15500, 14000, 12500, 11500, 12500, 14500, 17500, 19500, 22500, 24500, 23500, 21000, 18500, 16500, 14500, 13000, 12000]
gen_hydro = [500, 480, 450, 450, 450, 500, 550, 600, 550, 500, 480, 500, 550, 650, 750, 850, 950, 900, 800, 700, 650, 600, 550, 500]

gen_pumped_gen = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 500, 1200, 1500, 1200, 800, 400, 0, 0, 0, 0]
gen_pumped_load = [-1000, -1200, -1500, -1500, -1200, -800, 0, 0, -800, -1500, -1500, -800, 0, 0, 0, 0, 0, 0, 0, 0, -500, -800, -800, -1000]
gen_ess_dis = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 300, 800, 1000, 800, 500, 200, 0, 0, 0, 0]
gen_ess_chg = [-200, -300, -400, -400, -300, -200, 0, 0, -500, -800, -1000, -500, 0, 0, 0, 0, 0, 0, 0, 0, -100, -200, -200, -200]

gen_wind = [380, 350, 320, 310, 300, 310, 320, 350, 380, 390, 400, 380, 360, 350, 340, 330, 350, 380, 400, 390, 380, 370, 360, 350]
gen_solar = [0, 0, 0, 0, 0, 800, 2500, 5500, 8500, 10500, 11500, 11000, 9500, 7500, 5000, 2500, 500, 0, 0, 0, 0, 0, 0, 0]

gen_vre = [s + w for s, w in zip(gen_solar, gen_wind)]
net_load = [a - v for a, v in zip(actual_demand, gen_vre)]
spread_flex = [p_g + p_l + e_d + e_c for p_g, p_l, e_d, e_c in zip(gen_pumped_gen, gen_pumped_load, gen_ess_dis, gen_ess_chg)]

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
2. 반드시 모든 문장은 명사형/단어 종결형(개조식)으로 끝낼 것 (예: ~관찰됨, ~수준임, ~판단됨).
3. 출력 형식은 HTML <ul> 및 <li> 태그만 사용.
"""

prompt_chart4 = f"""
당신은 전력시장 분석가입니다.
금일 '발전원별 수급 구성 및 순부하(Net Load)' 현황에 대한 의견을 1~2문장의 개조식(명사 종결형)으로 작성하세요.
- 데이터: 신재생 최대 {max(gen_vre)}MW, 순부하 최저 {min(net_load)}MW
- 가이드: 팩트 위주의 분석을 HTML 없이 순수 텍스트로만 출력.
"""

try:
    model = genai.GenerativeModel("gemini-2.0-flash")
    ai_summary = model.generate_content(prompt_main).text.replace('```html', '').replace('```', '').strip()
    ai_gen_summary = model.generate_content(prompt_chart4).text.replace('```html', '').replace('
