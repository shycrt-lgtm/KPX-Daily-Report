# -*- coding: utf-8 -*-
"""data.json(전체, 약 10MB)에서 최근 N일만 뽑아 data_recent.json(작은 파일)을 만든다.
화면은 이 작은 파일을 먼저 읽어 빠르게 열리고, 옛날 날짜를 고를 때만 data.json을 읽는다."""
import json, os
N_DAYS = 60
if not os.path.exists("data.json"):
    raise SystemExit("data.json 없음")
d = json.load(open("data.json", encoding="utf-8"))
keys = sorted(d["days"])[-N_DAYS:]
out = {k: v for k, v in d.items() if k != "days"}
out["days"] = {k: d["days"][k] for k in keys}
new = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
old = open("data_recent.json", encoding="utf-8").read() if os.path.exists("data_recent.json") else ""
if new != old:
    open("data_recent.json", "w", encoding="utf-8").write(new)
    print(f">> [최근데이터] data_recent.json 갱신: {len(keys)}일, {len(new)/1024:.0f}KB (전체 {os.path.getsize('data.json')/1024/1024:.1f}MB)")
else:
    print(">> [최근데이터] 변경 없음")
