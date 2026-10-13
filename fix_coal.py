# -*- coding: utf-8 -*-
"""과거자료(2022.01~2026.06) 석탄 발전량이 0으로 들어간 것을 바로잡는 1회용 스크립트.
원인: 원본 엑셀에 비어 있는 '석탄' 열이 있어, 실제 값이 든 '유연탄'+'국내탄' 열 대신 빈 열(0)을 읽었음.
coal_hourly.json(원본 엑셀에서 유연탄+국내탄을 시간대별로 계산해 둔 값)으로 석탄이 0인 날만 채우고,
석탄이 들어가는 순부하(net_load)도 함께 다시 계산한다. 여러 번 실행해도 결과는 같다."""
import json

d = json.load(open("data.json", encoding="utf-8"))
coal = json.load(open("coal_hourly.json", encoding="utf-8"))
fixed = 0
for k, day in d["days"].items():
    g = day.get("gen")
    if not g or k not in coal:
        continue
    old = g.get("coal") or [0] * 24
    if sum(old) != 0:              # 이미 석탄 값이 있는 날은 건드리지 않음
        continue
    new = coal[k]
    g["coal"] = new
    if g.get("net_load"):
        g["net_load"] = [round(g["net_load"][i] + new[i], 4) for i in range(24)]
    fixed += 1
json.dump(d, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(f">> [석탄 보정] {fixed}일 반영 완료")
