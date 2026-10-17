# -*- coding: utf-8 -*-
"""월별 요약 → monthly_summary.json (에이전트·화면용 작은 파일)

입력
  - data.json         : 일별 전원별 시간대 출력(MW, 시간 평균), 최대전력·예비율
  - total_demand.json : 국가 총수요 시간대별(MW) — 있으면 반영, 없으면 해당 항목 생략
산출(월별)
  - 전원별 발전량(GWh) = 일별 시간대 출력(MW) 합계 / 1000   ※ 계통 출력(발전단, 육지) 기준
  - 최대전력(월 최대·발생일시), 월평균 일최대전력, 평균 공급예비율
  - 국가 총수요(GWh): 시장수요·PPA 추정·BTM 추정·합계, 최대 시간대 값
  - 수록 일수, 결측·복사 의심 일자
"""
import json, os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUT = "monthly_summary.json"
START_YM = os.environ.get("MONTHLY_START", "202201")
GEN_KEYS = ["nuclear", "coal", "gas", "oil", "hydro", "pump_gen", "pump_load",
            "ess_dis", "ess_chg", "wind", "solar", "net_load"]


def days_in_month(ym):
    y, m = int(ym[:4]), int(ym[4:])
    nxt = datetime(y + (m == 12), m % 12 + 1, 1)
    return (nxt - datetime(y, m, 1)).days


def r1(x):
    return round(x, 1) if x is not None else None


def main():
    master = json.load(open("data.json", encoding="utf-8"))
    days = master["days"]
    demand = {}
    if os.path.exists("total_demand.json"):
        demand = json.load(open("total_demand.json", encoding="utf-8")).get("days", {})

    months = sorted({d[:6] for d in days if d[:6] >= START_YM})
    out_months = {}
    prev_gen = None
    for ym in months:
        ks = sorted(d for d in days if d.startswith(ym))
        n_cal = days_in_month(ym)

        # ── 전원별 발전량 ──
        gen_sum = {k: 0.0 for k in GEN_KEYS}
        gen_days, copied, no_ess = 0, [], 0
        prev_gen = days.get((datetime.strptime(ks[0], "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d"), {}).get("gen")
        for d in ks:
            g = days[d].get("gen") or {}
            if not all(len(g.get(k, [])) == 24 for k in GEN_KEYS):
                prev_gen = g or prev_gen
                continue
            if prev_gen and g.get("nuclear") == prev_gen.get("nuclear") and g.get("coal") == prev_gen.get("coal"):
                copied.append(d)          # 전일 값 복사 의심(원자력·석탄 시간대 값이 전일과 완전히 같음)
            if not any(g["ess_dis"]) and not any(g["ess_chg"]):
                no_ess += 1
            for k in GEN_KEYS:
                gen_sum[k] += sum(g[k])
            gen_days += 1
            prev_gen = g
        gen = {k: r1(v / 1000) for k, v in gen_sum.items()}            # MW·h 합계 → GWh

        # ── 최대전력·예비율 ──
        peaks = [(days[d].get("cap_peak"), d, days[d].get("cap_time")) for d in ks if days[d].get("cap_peak")]
        res = [days[d]["cap_res"] for d in ks if days[d].get("cap_res") is not None]
        peak = max(peaks) if peaks else None

        rec = {
            "days_in_data": len(ks),
            "days_in_month": n_cal,
            "complete": len(ks) == n_cal and gen_days == n_cal,
            "first_day": ks[0], "last_day": ks[-1],
            "generation_gwh": gen,
            "generation_days": gen_days,
            "gen_copied_days": copied,
            "gen_days_without_ess": no_ess,
            "peak_mw": peak[0] if peak else None,
            "peak_date": peak[1] if peak else None,
            "peak_time": peak[2] if peak else None,
            "avg_daily_peak_mw": r1(sum(p[0] for p in peaks) / len(peaks)) if peaks else None,
            "avg_reserve_rate_pct": round(sum(res) / len(res), 2) if res else None,
        }

        # ── 국가 총수요 ──
        dks = sorted(d for d in demand if d.startswith(ym))
        if dks:
            comps = ["market", "ppa", "btm", "total"]
            tot = {k: 0.0 for k in comps}
            hrs = {k: 0 for k in comps}
            mx = None
            for d in dks:
                for h in range(24):
                    for k in comps:
                        v = (demand[d].get(k) or [None] * 24)[h]
                        if v is not None:
                            tot[k] += v
                            hrs[k] += 1
                    v = (demand[d].get("total") or [None] * 24)[h]
                    if v is not None and (mx is None or v > mx[0]):
                        mx = (v, d, f"{h:02d}시대")
            full = n_cal * 24
            rec["national_demand"] = {
                "days": len(dks),
                "hours_available": hrs,
                "complete": all(hrs[k] == full for k in comps),
                "energy_gwh": {k: (r1(tot[k] / 1000) if hrs[k] == full else None) for k in comps},
                "partial_gwh": {k: r1(tot[k] / 1000) for k in comps if 0 < hrs[k] < full},
                "max_total_mw": mx[0] if mx else None,
                "max_total_at": f"{mx[1]} {mx[2]}" if mx else None,
            }
        out_months[ym] = rec

    out = {
        "latest_date": master.get("latest_date"),
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "meta": {
            "region": "육지 기준(발전량·최대전력·예비율). 국가 총수요는 원 API 범위를 따름(확인 필요)",
            "generation_gwh": "전원별 계통 출력(발전단) 시간 평균 MW의 월 합계 / 1000. EPSIS 전력거래량(정산·송전단)과 기준이 달라 같은 값이 아님 "
                              "(예: 2025-09 원자력 14,305.7 vs EPSIS 거래량 13,520.6 GWh). EPSIS 값을 대체하지 말고 '속보 추정치'로만 사용",
            "generation_keys": "nuclear 원자력 / coal 석탄(유연탄+국내탄) / gas LNG / oil 유류 / hydro 수력 / pump_gen 양수 발전 / "
                               "pump_load 양수 펌핑(음수) / ess_dis ESS 방전 / ess_chg ESS 충전(음수) / wind 풍력 / solar 태양광(시장) / net_load 순공급 합계",
            "gen_copied_days": "원자력·석탄 시간대 값이 전일과 완전히 같은 날(수집 실패로 전일 값이 남았을 가능성). 해당 월 합계 신뢰도 낮음",
            "gen_days_without_ess": "ESS 충방전이 모두 0인 날 수(ESS 미제공 자료로 채운 날일 수 있음)",
            "avg_daily_peak_mw": "일별 최대전력의 월 평균 (EPSIS '최대/최소전력 월별평균'과 같은 개념)",
            "national_demand": "시장수요 + PPA 추정 + BTM 추정 (MW 시간 평균의 월 합계 / 1000 = GWh). 항목별 수록 시간은 hours_available(월 전체 = 일수×24). energy_gwh는 해당 항목이 월 전체 수록된 경우만 값이 있고, 일부만 있으면 partial_gwh(수록 시간 합계, 월 합계 아님)에 표시. PPA·BTM은 매시간 1회 표본, 2026-10-17부터 수집",
            "smp": "월 SMP는 수록하지 않음 — KPX 공표 월별 SMP를 사용할 것",
        },
        "months": out_months,
    }
    new = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    if new != old:
        open(OUT, "w", encoding="utf-8").write(new)
    print(f">> [월별요약] {len(out_months)}개월, {len(new)/1024:.0f}KB, 최신 {out['latest_date']}")


if __name__ == "__main__":
    main()
