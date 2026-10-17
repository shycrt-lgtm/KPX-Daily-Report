# -*- coding: utf-8 -*-
"""국가 총수요 추정 → demand_estimate.json

API(total_demand.json)가 비거나 실패해도, 이미 수집 중인 전원별 발전량(data.json)으로 시간대별 국가 총수요를 채운다.
시간대마다 API 실측이 있으면 실측을, 없으면 추정을 쓰고 출처를 표시한다.

추정 방법 (보정 계수는 최근 API 실측과 비교해 매번 다시 계산)
  1) 시장수요 = 전원별 출력 합계(원자력·석탄·유류·LNG·수력·양수·ESS·풍력·태양광) + 보정값(offset)
     offset = 최근 CALIB_DAYS일 'API 시장수요 − 출력 합계'의 중앙값 (대시보드에 없는 기타 신재생 등)
  2) PPA 추정 = 시장 태양광 × ratio_ppa,  BTM 추정 = 시장 태양광 × ratio_btm
     ratio = 최근 CALIB_DAYS일 낮 시간(시장 태양광 ≥ MIN_SOLAR MW) 'API 값 ÷ 시장 태양광'의 중앙값
     PPA·BTM이 태양광에서 나온다는 가정(야간 0)에 기반. 표본이 MIN_PAIRS개 미만이면 추정하지 않음.
  3) 국가 총수요 = 시장수요 + PPA + BTM
출처 코드(src): A = API 실측, E = 추정, - = 값 없음
"""
import json, os, statistics as st
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUT = "demand_estimate.json"
CALIB_DAYS = int(os.environ.get("EST_CALIB_DAYS", "30"))
EST_START = os.environ.get("EST_START", "20250101")      # 소급 추정 시작일
MIN_SOLAR = 1500.0
MIN_PAIRS_OFFSET = 48
MIN_PAIRS_RATIO = 6
GEN_SUM_KEYS = ["nuclear", "coal", "oil", "gas", "hydro", "pump_gen", "pump_load",
                "ess_dis", "ess_chg", "wind", "solar"]


def gen_sum(g, h):
    try:
        return sum(g[k][h] for k in GEN_SUM_KEYS)
    except (KeyError, IndexError, TypeError):
        return None


def r1(x):
    return round(x, 1) if x is not None else None


def main():
    days = json.load(open("data.json", encoding="utf-8"))["days"]
    obs = json.load(open("total_demand.json", encoding="utf-8"))["days"] if os.path.exists("total_demand.json") else {}

    # ── 보정 계수: 최근 CALIB_DAYS일 실측과 비교 ──
    cutoff = (datetime.now(KST) - timedelta(days=CALIB_DAYS)).strftime("%Y%m%d")
    off_pairs, ppa_pairs, btm_pairs = [], [], []
    day_off = {}                                        # 날짜별 offset 중앙값(시점별 보정용)
    for d, o in obs.items():
        if d not in days:
            continue
        g = days[d].get("gen") or {}
        dv = [m - s for h in range(24)
              for s, m in [(gen_sum(g, h), (o.get("market") or [None] * 24)[h])] if s is not None and m is not None]
        if len(dv) >= 12:
            day_off[d] = st.median(dv)
        if d < cutoff:
            continue
        for h in range(24):
            s = gen_sum(g, h)
            m = (o.get("market") or [None] * 24)[h]
            if s is not None and m is not None:
                off_pairs.append(m - s)
            sol = (g.get("solar") or [0] * 24)[h]
            if sol >= MIN_SOLAR:
                p, b = (o.get("ppa") or [None] * 24)[h], (o.get("btm") or [None] * 24)[h]
                if p is not None:
                    ppa_pairs.append(p / sol)
                if b is not None:
                    btm_pairs.append(b / sol)
    offset = st.median(off_pairs) if len(off_pairs) >= MIN_PAIRS_OFFSET else None
    ratio_ppa = st.median(ppa_pairs) if len(ppa_pairs) >= MIN_PAIRS_RATIO else None
    ratio_btm = st.median(btm_pairs) if len(btm_pairs) >= MIN_PAIRS_RATIO else None
    calib = {
        "market_offset_mw": r1(offset), "market_offset_sd_mw": r1(st.pstdev(off_pairs)) if len(off_pairs) > 1 else None,
        "market_pairs": len(off_pairs),
        "ratio_ppa": round(ratio_ppa, 4) if ratio_ppa is not None else None,
        "ratio_btm": round(ratio_btm, 4) if ratio_btm is not None else None,
        "ratio_pairs": {"ppa": len(ppa_pairs), "btm": len(btm_pairs)},
        "window": f"최근 {CALIB_DAYS}일 (시장수요 추정은 날짜별로 전후 15일 실측 기준)",
        "market_observed_range": f"{off_dates[0]}~{off_dates[-1]}" if (off_dates := sorted(day_off)) else None,
    }

    off_dates = sorted(day_off)

    def offset_for(d):
        """d 전후 15일 실측의 offset 중앙값. 실측 범위 밖이면 가장 가까운 끝 구간 값(범위 밖 표시)."""
        if not off_dates:
            return offset, "recent"
        dt = datetime.strptime(d, "%Y%m%d")
        lo, hi = (dt - timedelta(days=15)).strftime("%Y%m%d"), (dt + timedelta(days=15)).strftime("%Y%m%d")
        near = [day_off[x] for x in off_dates if lo <= x <= hi]
        if len(near) >= 3:
            return st.median(near), "window"
        edge = off_dates[:15] if d < off_dates[0] else off_dates[-15:]
        return st.median(day_off[x] for x in edge), "outside"

    # ── 시간대별 최선값(실측 우선, 없으면 추정) ──
    out_days = {}
    for d in sorted(set(days) | set(obs)):
        if d < EST_START:
            continue
        g = (days.get(d) or {}).get("gen") or {}
        o = obs.get(d, {})
        off_d, off_basis = offset_for(d) if offset is not None else (None, None)
        rec = {k: [None] * 24 for k in ("market", "ppa", "btm", "total")}
        src = {k: ["-"] * 24 for k in ("market", "ppa", "btm")}
        for h in range(24):
            m = (o.get("market") or [None] * 24)[h]
            if m is not None:
                rec["market"][h], src["market"][h] = m, "A"
            elif off_d is not None and gen_sum(g, h) is not None:
                rec["market"][h], src["market"][h] = r1(gen_sum(g, h) + off_d), "E"
            sol = (g.get("solar") or [None] * 24)[h]
            for k, ratio in (("ppa", ratio_ppa), ("btm", ratio_btm)):
                v = (o.get(k) or [None] * 24)[h]
                if v is not None:
                    rec[k][h], src[k][h] = v, "A"
                elif ratio is not None and sol is not None:
                    rec[k][h], src[k][h] = r1(max(0.0, sol) * ratio), "E"
            if None not in (rec["market"][h], rec["ppa"][h], rec["btm"][h]):
                rec["total"][h] = r1(rec["market"][h] + rec["ppa"][h] + rec["btm"][h])
        if all(v is None for v in rec["market"]):
            continue
        rec["src"] = {k: "".join(v) for k, v in src.items()}      # 24자 문자열, 시간대별 A/E/-
        if "E" in rec["src"]["market"]:
            rec["market_offset_mw"], rec["market_calib"] = r1(off_d), off_basis
        out_days[d] = rec

    out = {
        "latest_date": max(out_days) if out_days else None,
        "calibration": calib,
        "meta": {
            "unit": "MW, 시간대별(0번 = 00시대)",
            "fields": "market 시장수요 / ppa PPA 추정 / btm BTM 추정 / total 국가 총수요(합계)",
            "src": "시간대별 출처 문자열 — A: API 실측(total_demand.json), E: 추정, -: 값 없음",
            "market_estimate": "전원별 출력 합계(data.json) + 보정값. 보정값은 해당일 전후 15일 실측 기준(market_calib=window), "
                               "실측 범위 밖 날짜는 가장 가까운 15일 기준(market_calib=outside, 오차 커질 수 있음)",
            "ppa_btm_estimate": "시장 태양광 출력 × ratio (PPA·BTM이 태양광 기반이라는 가정)",
            "caution": "추정(E) 값은 [추정]으로 표기할 것. 보정 계수는 최근 기간 기준이므로 과거로 갈수록 오차가 커질 수 있음"
                       "(특히 PPA·BTM은 설비용량 증가에 따라 비율이 달라짐). ratio가 null이면 PPA·BTM 추정 불가",
            "range": f"{EST_START} 이후",
        },
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "days": out_days,
    }
    new = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    if new != old:
        open(OUT, "w", encoding="utf-8").write(new)
    print(f">> [총수요추정] {len(out_days)}일, {len(new)/1024:.0f}KB | offset {calib['market_offset_mw']} MW "
          f"(표본 {len(off_pairs)}) | ratio PPA {calib['ratio_ppa']} BTM {calib['ratio_btm']} (표본 {calib['ratio_pairs']})")


if __name__ == "__main__":
    main()
