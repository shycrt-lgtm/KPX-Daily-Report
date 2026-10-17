# -*- coding: utf-8 -*-
"""웹 속보치 vs 공표값 대조표 → published_check.json
입력: monthly_summary.json(속보치·KPX 월 SMP), published_manual.json(EPSIS 등 수기 입력 공표값)
월별·지표별로 속보치, 공표값, 차이(절대·%)를 기록하고, 지표별 누적 오차 통계를 낸다."""
import json, os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUT = "published_check.json"

# (지표 이름, 속보치 키, 공표값 위치, 단위)
SCALARS = [
    ("월 SMP(육지)", "smp_land_weighted_est", ("summary", "smp_land_published"), "원/kWh"),
    ("최대전력", "peak_mw", ("manual", "peak_mw"), "MW"),
    ("최대전력 시 공급능력", "peak_supply_capacity_mw", ("manual", "peak_supply_capacity_mw"), "MW"),
    ("공급예비율", "peak_reserve_rate_pct", ("manual", "peak_reserve_rate_pct"), "%"),
]
# 웹 발전량 키 → EPSIS 거래량 항목(합산)
GEN_MAP = [("원자력", ["nuclear"], ["원자력"]), ("석탄", ["coal"], ["유연탄", "무연탄"]),
           ("LNG", ["gas"], ["LNG"]), ("양수", ["pump_gen"], ["양수"]), ("유류", ["oil"], ["유류"])]


def diff(est, pub):
    if est is None or pub is None:
        return None, None
    d = round(est - pub, 2)
    return d, (round(d / pub * 100, 2) if pub else None)


def main():
    summ = json.load(open("monthly_summary.json", encoding="utf-8"))["months"]
    man = json.load(open("published_manual.json", encoding="utf-8")).get("months", {}) if os.path.exists("published_manual.json") else {}
    out_m, stats = {}, {}
    for ym in sorted(summ):
        s, m = summ[ym], man.get(ym, {})
        rows = []
        for name, ek, (where, pk), unit in SCALARS:
            pub = (s if where == "summary" else m).get(pk)
            est = s.get(ek)
            if pub is None:
                continue
            d, pct = diff(est, pub)
            rows.append({"지표": name, "단위": unit, "속보치": est, "공표값": pub, "차이": d, "차이_pct": pct,
                         "공표_출처": "KPX 홈페이지 월별 SMP" if where == "summary" else m.get("source")})
        gt = m.get("gen_trade_gwh") or {}
        for name, wk, ek in GEN_MAP:
            if not all(k in gt for k in ek):
                continue
            pub = round(sum(gt[k] for k in ek), 1)
            est = round(sum(s["generation_gwh"][k] for k in wk), 1)
            d, pct = diff(est, pub)
            rows.append({"지표": f"발전량 {name}", "단위": "GWh", "속보치": est, "공표값": pub, "차이": d, "차이_pct": pct,
                         "공표_출처": m.get("source"), "비고": "속보치는 발전단 출력, 공표값은 EPSIS 전력거래량(기준 다름)"})
        if rows:
            out_m[ym] = {"complete": s.get("complete"), "smp_published_first_seen": s.get("smp_published_first_seen"), "rows": rows}
            for r in rows:
                if r["차이_pct"] is not None:
                    stats.setdefault(r["지표"], []).append(r["차이_pct"])
    summary = {k: {"months": len(v), "mean_pct": round(sum(v) / len(v), 2),
                   "max_abs_pct": round(max(abs(x) for x in v), 2)} for k, v in stats.items()}
    out = {
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "meta": {"차이": "속보치 − 공표값", "차이_pct": "차이 ÷ 공표값 × 100",
                 "error_stats": "지표별 누적 오차(공표값이 있는 월만). 속보치를 어디까지 믿을지 판단 근거",
                 "입력": "EPSIS 공표값은 published_manual.json에 입력, KPX 월 SMP는 자동"},
        "error_stats": summary,
        "months": out_m,
    }
    new = json.dumps(out, ensure_ascii=False, indent=1)
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    if new.split('"meta"')[1] != (old.split('"meta"')[1] if '"meta"' in old else ""):
        open(OUT, "w", encoding="utf-8").write(new)
    print(f">> [대조표] {len(out_m)}개월, 지표 {len(summary)}개")


if __name__ == "__main__":
    main()
