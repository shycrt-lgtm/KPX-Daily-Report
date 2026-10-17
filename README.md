# KPX-Daily-Report

전력거래소(KPX) 공개자료를 매일 수집해 전력시장 전일 실적 대시보드와 데이터 파일을 게시합니다.
대시보드: https://shycrt-lgtm.github.io/KPX-Daily-Report/ (화면 표시용 — 데이터는 아래 JSON 파일로 조회)

## 데이터 파일 (용도별로 필요한 파일 하나만 열람)

| 파일 | 크기 | 내용 |
|---|---|---|
| `monthly_summary.json` | 약 30KB | 월별 전원별 발전량(GWh), 최대전력, 월평균 일최대전력, 평균 예비율, 국가 총수요 |
| `smp_live.json` | 수 KB | 금일 하루전시장 SMP |
| `data_recent.json` | 약 350KB | 최근 60일 일별 상세(시간대별 SMP·전원별 출력) + LNG 요금 |
| `total_demand.json` | 누적 | 국가 총수요 시간대별(시장수요·PPA 추정·BTM 추정) |
| `data.json` | 약 10MB | 2022년 이후 전체 일별 상세 — 과거 기간이 필요할 때만 |

공통: 육지 기준(국가 총수요 제외), 전일 실적 기준(실시간 아님). `latest_date`가 마지막 수록일.

## monthly_summary.json

`months.{YYYYMM}`
- `generation_gwh`: 전원별 발전량(GWh) = 일별 시간대 출력(MW, 시간 평균)의 월 합계 ÷ 1000
  - 키: nuclear 원자력 / coal 석탄(유연탄+국내탄) / gas LNG / oil 유류 / hydro 수력 / pump_gen 양수 발전 / pump_load 양수 펌핑(음수) / ess_dis ESS 방전 / ess_chg ESS 충전(음수) / wind 풍력 / solar 태양광(시장) / net_load 순공급 합계
  - **계통 출력(발전단) 기준으로 EPSIS 전력거래량(정산 기준)과 같은 값이 아님.** 예: 2025-09 원자력 14,305.7 GWh vs EPSIS 거래량 13,520.6 GWh. EPSIS 미게시 월의 속보 추정치로만 사용
- `complete`: 해당 월 전 일자 수록 여부. false면 부분 합계
- `gen_copied_days`: 원자력·석탄 값이 전일과 같아 수집 실패가 의심되는 날
- `gen_days_without_ess`: ESS 값이 0인 날 수(ESS 미제공 자료 기간)
- `peak_mw`, `peak_date`, `peak_time`: 월 최대전력과 발생 일시
- `avg_daily_peak_mw`: 일최대전력의 월 평균
- `avg_reserve_rate_pct`: 일별 공급예비율(최대전력 시점)의 월 단순평균
- `national_demand`: 국가 총수요(있는 월만) — `energy_gwh`(market 시장수요 / ppa PPA 추정 / btm BTM 추정 / total 합계), `hours_available`(항목별 수록 시간), `max_total_mw`
- 월 SMP는 수록하지 않음 → KPX 공표 월별 SMP 사용

## data_recent.json / data.json

`days.{YYYYMMDD}`
- `smp_hourly`: 시간대별 육지 SMP 24개(원/kWh). 0번 = 1시, 23번 = 24시
- `smp_avg`: 일 SMP 평균(원/kWh). KPX 공표 가중평균 방식(수요예측 가중), 수요예측이 없으면 단순평균
- `smp_max` / `smp_min`: 일 최고·최저(원/kWh)
- `cap_peak` / `cap_time` / `cap_res`: 최대전력(MW), 발생 시각, 공급예비율(%)
- `recent_7[]`: 최근 7일 avg·max·min(SMP), cap 공급능력(MW), peak 최대전력(MW), time, res 예비율(%), is_holiday
- `gen.{전원}`: 전원별 시간대 출력(MW, 5분 자료의 시간 평균). 0번 = 00시대. 음수는 양수 펌핑·ESS 충전
- `lng_price`: 당월 적용 발전용 LNG 요금(한국가스공사)
- `lng_monthly.{YYYYMM}`: 월별 발전용 LNG 요금. `src`가 "kogas"면 가스공사 공표 수집값, "seed"면 초기 입력값

※ `lng_price`·`lng_monthly`는 가스공사 발전용 요금이며, EPSIS 비용평가 연료단가(원/ton)·열량단가(원/Gcal)·연료비단가(원/kWh)와 다른 지표입니다.

## total_demand.json

`days.{YYYYMMDD}`: 시간대별 24개(MW, 0번 = 00시대)
- `market`: 시장수요(fuelPwrTot) — 5분 자료의 시간 평균, 하루 2회 수집(최근 10일 보정)
- `ppa` / `btm`: PPA 추정(pEsmw) / BTM 추정(bEmsw) — 매시간 1회 조회한 현재 5분 값(시간대 대표 표본). `sample_time`이 실제 기준시각
- `total`: market + ppa + btm. 세 값이 모두 있는 시간대만
- `complete`, `hours`(항목별 수록 시간 수)

출처: 시장수요 — 공공데이터포털 한국전력거래소_발전원별 발전량 현황조회(PwrAmountByGen) / PPA·BTM — 같은 기관 현황조회_GW(sumperfuel5m, 이력 없이 현재값만 제공).
PPA·BTM은 2026-10-17부터 수집하며, 이전 기간과 수집이 누락된 시간대는 null. 수집 상태는 `total_demand_status.json`.

`monthly_summary.json`의 `national_demand.energy_gwh`는 해당 항목이 월 전체 수록된 경우만 값이 있고, 일부만 있으면 `partial_gwh`(월 합계 아님)에 표시.

## 갱신

- `daily_report.yml`(매일 KST 00:00, 07:20): `build_report.py` → `collect_kpx_smp.py`·`apply_kpx_smp.py` → `make_recent.py` → `collect_total_demand.py`(시장수요) → `build_monthly.py`
- `smp_live.yml`(매시간): 하루전 SMP 수집 → `collect_total_demand.py --snapshot`(PPA·BTM) → `build_monthly.py`
