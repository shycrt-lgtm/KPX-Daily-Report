# -*- coding: utf-8 -*-
"""smp_live.json 중 전력거래소 표(src=kpx)에서 온 날의 SMP 로 data.json 의 해당 일자 SMP 관련 값을 맞춘다.
 (시간별 SMP, 평균/최고/최저, 피크구간, 전일대비, 최근 7일 표의 SMP 숫자)"""
import json, os
from datetime import datetime, timedelta

WK = ["월", "화", "수", "목", "금", "토", "일"]


def fmt_diff(v):
    if v > 0: return f"+{abs(v):.2f}원", "text-rose-600"
    if v < 0: return f"▼{abs(v):.2f}원", "text-blue-600"
    return "- 0.00원", "text-slate-500"


def label(k):
    d = datetime.strptime(k, "%Y%m%d")
    return f"{d.month}.{d.day}({WK[d.weekday()]})"


def peak(s, mx):
    mn = min(s); thr = mn + (mx - mn) * 0.85
    i = s.index(mx); l = r = i
    while l > 0 and s[l - 1] >= thr: l -= 1
    while r < 23 and s[r + 1] >= thr: r += 1
    if r - l > 6: l, r = max(0, i - 2), min(23, i + 2)
    return l, r


def main():
    if not (os.path.exists("smp_live.json") and os.path.exists("data.json")):
        print(">> [KPX 반영] 파일 없음"); return
    live = json.load(open("smp_live.json", encoding="utf-8")).get("days", {})
    data = json.load(open("data.json", encoding="utf-8"))
    days = data["days"]
    changed = []
    for k, v in sorted(live.items()):
        if v.get("src") != "kpx" or k not in days:
            continue
        d, s = days[k], v["smp"]
        mx, mn = round(max(s), 2), round(min(s), 2)
        avg = v.get("wavg") if v.get("wavg") is not None else d.get("smp_avg")
        l, r = peak(s, mx)
        new = {"smp_hourly": s, "smp_avg": avg, "smp_max": mx, "smp_min": mn, "smp_max_idx": s.index(mx),
               "smp_min_idx": s.index(mn), "peak_band": f"{l+1}~{r+1}시", "peak_left": l, "peak_right": r}
        if v.get("wavg") is not None:
            new["smp_basis"] = "weighted"
        if any(d.get(a) != b for a, b in new.items()):
            d.update(new); changed.append(k)
    if not changed:
        print(">> [KPX 반영] data.json 변경 없음"); return
    first = min(changed)
    for k in sorted(days):
        if k < first: continue
        d = days[k]
        p = (datetime.strptime(k, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        if p in days:
            for a, b in (("avg", "smp_avg"), ("max", "smp_max"), ("min", "smp_min")):
                d["diff_%s_txt" % a], d["diff_%s_color" % a] = fmt_diff(round(d[b] - days[p][b], 2))
        win = {label((datetime.strptime(k, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d")):
               (datetime.strptime(k, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d") for i in range(7)}
        for e in d.get("recent_7", []):
            kk = win.get(e.get("date"))
            if kk in days:
                e["avg"], e["max"], e["min"] = days[kk]["smp_avg"], days[kk]["smp_max"], days[kk]["smp_min"]
    json.dump(data, open("data.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f">> [KPX 반영] data.json 의 SMP 갱신: {', '.join(changed)}")


if __name__ == "__main__":
    main()
