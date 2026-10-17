# KPX-Daily-Report

전력거래소(KPX) 공개자료를 매일 수집해 전력시장 전일 실적 대시보드와 데이터 파일을 게시합니다.
대시보드: https://shycrt-lgtm.github.io/KPX-Daily-Report/ (화면 표시용 — 데이터는 아래 JSON 파일로 조회)

## 데이터 파일 (용도별로 필요한 파일 하나만 열람)

| 파일 | 크기 | 내용 |
|---|---|---|
| `monthly_summary.json` | 약 50KB | 월별 SMP(공표값·속보치), 전원별 발전량(GWh), 최대전력·공급능력·예비율, 국가 총수요 |
| `published_check.json` | 작음 | 속보치 vs 공표값 대조표, 지표별 누적 오차 |
| `kpx_monthly_smp.json` | 작음 | KPX 홈페이지 월별 SMP(육지·제주·통합) 공표값과 처음 확인한 날짜 |
| `published_manual.json` | 작음 | EPSIS 공표값 수기 입력용 — 공표 후 이 파일에 값을 넣으면 대조표에 반영 |
| `smp_live.json` | 수 KB | 금일 하루전시장 SMP |
| `data_recent.json` | 약 350KB | 최근 60일 일별 상세(시간대별 SMP·전원별 출력) + LNG 요금 |
| `demand_estimate.json` | 약 500KB | 국가 총수요 시간대별 최선값(API 실측 우선, 빈 시간은 추정) — 2025년 이후 |
| `total_demand.json` | 누적 | 국가 총수요 API 실측만(시장수요·PPA 추정·BTM 추정) |
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
- `smp_land_published`(jeju·total): KPX 홈페이지 월별 SMP 공표값. `smp_published_first_seen`: 처음 확인한 날짜(null = 수집 시작 전 게시)
- `smp_land_weighted_est`: 월 SMP 속보치(육지) = 시간대별 SMP를 시장수요로 가중평균. 2025-01~2026-09 21개월 대조 평균 −0.13%, 최대 0.5% 차이. 공표값이 없을 때만 사용
- `peak_supply_capacity_mw`, `peak_reserve_rate_pct`: 월 최대전력 발생일의 공급능력·예비율(EPSIS 월 실적과 같은 개념, 2025-09·2026-09 일치 확인)

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

## demand_estimate.json

API가 비거나 실패한 시간을 수집 중인 발전량으로 채운 국가 총수요. `days.{YYYYMMDD}`에 `market`·`ppa`·`btm`·`total` 24개(MW)와 `src`(시간대별 A=API 실측, E=추정, -=없음).
- 시장수요 추정 = 전원별 출력 합계 + 보정값(해당일 전후 15일 실측과의 차이 중앙값, 약 3,000~3,250 MW). 검증(2026-10-07~16): 일 에너지 오차 ±0.42% 이내. `market_calib`이 "outside"면 실측 범위(2026-05-20 이후) 밖이라 오차가 커질 수 있음
- PPA·BTM 추정 = 시장 태양광 × 비율(최근 30일 낮 시간 실측 중앙값). PPA·BTM이 태양광 기반이라는 가정. 실측 표본 6개 미만이면 추정하지 않음(null)
- 보정 계수는 `calibration`에 기록

`monthly_summary.json`의 `national_demand`는 이 파일 기준. `estimated_share_pct`가 0보다 크면 추정 포함, `energy_gwh`는 월 전체 시간이 채워진 항목만 값이 있음.

## 갱신

- `daily_report.yml`(매일 KST 00:00, 07:20): `build_report.py` → `collect_kpx_smp.py`·`apply_kpx_smp.py` → `make_recent.py` → `collect_total_demand.py`(시장수요) → `collect_kpx_monthly_smp.py` → `estimate_demand.py` → `build_monthly.py` → `build_check.py`
- `smp_live.yml`(매시간): 하루전 SMP 수집 → `collect_total_demand.py --snapshot`(PPA·BTM) → `estimate_demand.py` → `build_monthly.py` → `build_check.py`
