# -*- coding: utf-8 -*-
"""과거자료(2022.01~2026.06) 석탄 발전량이 0으로 들어간 것을 바로잡는 1회용 스크립트.
원인: 원본 엑셀에 비어 있는 '석탄' 열이 있어, 실제 값이 든 '유연탄'+'국내탄' 열 대신 빈 열(0)을 읽었음.
원본 엑셀은 저장소에서 지웠지만 변경 기록(git)에 남아 있으므로, 거기서 꺼내 시간대별 석탄을 계산한다.
석탄이 0인 날만 채우고 순부하(net_load)도 함께 다시 계산한다. 여러 번 실행해도 결과는 같다."""
import json, subprocess, io
import pandas as pd

def git(*a):
    return subprocess.run(["git", *a], capture_output=True, check=True).stdout

coal = {}
for y in range(2022, 2027):
    path = f"2. 시간대별 발전원별 발전량_{y}.xlsx"
    commits = git("log", "--format=%H", "--", path).decode().split()
    blob = None
    for c in commits:                       # 최근 기록부터, 파일이 남아 있는 버전을 찾음
        try:
            blob = git("show", f"{c}:{path}"); break
        except subprocess.CalledProcessError:
            try:
                blob = git("show", f"{c}^:{path}"); break
            except subprocess.CalledProcessError:
                continue
    if not blob:
        print(f">> [석탄 보정] {y}년 원본 엑셀을 기록에서 찾지 못함"); continue
    df = pd.read_excel(io.BytesIO(blob))
    t = pd.to_datetime(df["일시"])
    for c in ["석탄", "유연탄", "국내탄"]:
        if c not in df: df[c] = None
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    df["coal"] = df["석탄"].fillna(df["유연탄"].fillna(0) + df["국내탄"].fillna(0))
    df["d"] = t.dt.strftime("%Y%m%d"); df["h"] = t.dt.hour
    g = df.groupby(["d", "h"])["coal"].mean().reset_index()
    n = 0
    for d, x in g.groupby("d"):
        if len(x) < 24: continue
        coal[d] = [round(v, 4) for v in x.sort_values("h")["coal"].tolist()]; n += 1
    print(f">> [석탄 보정] {y}년 원본에서 {n}일 계산")

data = json.load(open("data.json", encoding="utf-8"))
fixed = 0
for k, day in data["days"].items():
    g = day.get("gen")
    if not g or k not in coal:
        continue
    if sum(g.get("coal") or [0]) != 0:      # 이미 석탄 값이 있는 날은 건드리지 않음
        continue
    new = coal[k]
    g["coal"] = new
    if g.get("net_load"):
        g["net_load"] = [round(g["net_load"][i] + new[i], 4) for i in range(24)]
    fixed += 1
if fixed:
    json.dump(data, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(f">> [석탄 보정] {fixed}일 반영 완료")
