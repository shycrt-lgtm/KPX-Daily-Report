# -*- coding: utf-8 -*-
"""국가 총수요(시장수요 + PPA 추정 + BTM 추정) 시간대별 수집 → total_demand.json

두 가지 방식으로 나눠 수집한다.
  1) 기본 실행 (하루 2회, daily_report.yml)
     공공데이터포털 '한국전력거래소_발전원별 발전량 현황조회'(PwrAmountByGen)
     → fuelPwrTot(시장수요) 5분 자료를 시간대별 평균으로. 최근 LOOKBACK_DAYS일을 다시 받아 보정.
  2) --snapshot 실행 (매시간, smp_live.yml)
     공공데이터포털 '한국전력거래소_발전원별 발전량 현황조회_GW'(sumperfuel5m)
     → 이 API는 이력 없이 '현재 5분 값 1건'만 제공. 매시간 1건을 받아 그 시간대의 PPA·BTM 대표값으로 기록.
     (하루 24회 호출 — 개발계정 일 100회 한도 이내)

국가 총수요(total) = 시장수요(시간 평균) + PPA 추정(시간당 1개 표본) + BTM 추정(시간당 1개 표본)
세 값이 모두 있는 시간대만 total을 계산한다.
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
API_KEY = os.environ.get("DATA_GO_KR_KEY", "")
MARKET_API = "https://apis.data.go.kr/B552115/PwrAmountByGen/getPwrAmountByGen"
SNAPSHOT_API = os.environ.get("DEMAND_API_URL") or "https://apis.data.go.kr/B552115/sumperfuel5m/getSumperfuel5m"
OUT = "total_demand.json"
STATUS = "total_demand_status.json"
LOOKBACK_DAYS = int(os.environ.get("DEMAND_LOOKBACK_DAYS", "10"))
MAX_PAGES = 40
DIAG = []


# ── 공통 ────────────────────────────────────────────────────────────────
def now_kst():
    return datetime.now(KST)


def http_json(url, params, attempts=3):
    for i in range(1, attempts + 1):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=(20, 60))
            body = r.text.replace(API_KEY, "***") if API_KEY else r.text
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    DIAG.append(f"{url.rsplit('/', 1)[-1]} HTTP200 비JSON: {body[:200]}")
            else:
                DIAG.append(f"{url.rsplit('/', 1)[-1]} HTTP{r.status_code}: {body[:200]}")
        except Exception as e:
            DIAG.append(f"{url.rsplit('/', 1)[-1]} 통신 실패 {type(e).__name__}")
        print(">> [총수요]", DIAG[-1])
        time.sleep(5 * i)
    return None


def items_of(data):
    root = data.get("response", data) if isinstance(data, dict) else {}
    body = root.get("body", {}) if isinstance(root, dict) else {}
    items = body.get("items", {}) if isinstance(body, dict) else {}
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return items if isinstance(items, list) else []


def dt_of(x):
    raw = x.get("baseDatetime") or x.get("baseDateTime") or ""
    return "".join(ch for ch in str(raw) if ch.isdigit())[:14]


def num(x, key):
    try:
        return float(str(x.get(key)).replace(",", ""))
    except (TypeError, ValueError):
        return None


def load():
    if os.path.exists(OUT):
        return json.load(open(OUT, encoding="utf-8"))
    return {"days": {}}


def blank_day():
    return {k: [None] * 24 for k in ("market", "ppa", "btm", "total")} | {"sample_time": [None] * 24}


def recompute(day):
    for k in ("market", "ppa", "btm", "total", "sample_time"):
        day.setdefault(k, [None] * 24)
    day["total"] = [round(m + p + b, 1) if None not in (m, p, b) else None
                    for m, p, b in zip(day["market"], day["ppa"], day["btm"])]
    day["complete"] = all(v is not None for v in day["total"])
    day["hours"] = {k: sum(v is not None for v in day[k]) for k in ("market", "ppa", "btm", "total")}


def save(store, mode, note, ok=True):
    store["latest_date"] = max(store["days"]) if store["days"] else None
    store["meta"] = {
        "unit": "MW",
        "market": "시장수요 fuelPwrTot — 5분 자료의 시간대별 평균 (PwrAmountByGen)",
        "ppa": "PPA 추정 pEsmw — 매시간 1회 조회한 5분 값(시간대 대표 표본, sumperfuel5m)",
        "btm": "BTM 추정 bEmsw — 매시간 1회 조회한 5분 값(시간대 대표 표본, sumperfuel5m)",
        "total": "market + ppa + btm (국가 총수요, 산출). 세 값이 모두 있는 시간대만",
        "sample_time": "PPA·BTM 표본의 실제 기준시각(HHMM)",
        "hour_index": "배열 0번 = 00시대, 23번 = 23시대",
        "region": "API 설명상 육지·제주 포함 — 확인 필요",
        "note": "PPA·BTM은 2026-10-17부터 수집. 이전 기간은 null",
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M KST"),
    }
    new = json.dumps(store, ensure_ascii=False, separators=(",", ":"))
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    if new != old:
        open(OUT, "w", encoding="utf-8").write(new)
    status = json.load(open(STATUS, encoding="utf-8")) if os.path.exists(STATUS) else {}
    status[mode] = {"ok": ok, "note": note, "log": DIAG[-4:], "at": now_kst().strftime("%Y-%m-%d %H:%M KST")}
    json.dump(status, open(STATUS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ── 1) 시장수요: 5분 자료 → 시간 평균 ───────────────────────────────────
def run_market():
    store = load()
    oldest = (now_kst() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    today = now_kst().strftime("%Y%m%d")
    rows, page = {}, 1
    while page <= MAX_PAGES:
        data = http_json(MARKET_API, {"serviceKey": API_KEY, "pageNo": page, "numOfRows": 1000, "dataType": "json"})
        items = items_of(data) if data else []
        if not items:
            break
        seen = None
        for x in items:
            dt, v = dt_of(x), num(x, "fuelPwrTot")
            if len(dt) < 10 or v is None:
                continue
            rows.setdefault(dt[:8], {}).setdefault(int(dt[8:10]), []).append(v)
            seen = dt[:8] if seen is None or dt[:8] < seen else seen
        if seen is None or seen < oldest:
            break
        page += 1
    if not rows:
        save(store, "market", "자료 미수신 — 기존 값 유지", ok=False)
        sys.exit(">> [총수요-시장수요] 자료를 받지 못했습니다")
    changed = 0
    for d, hrs in rows.items():
        if d < oldest or d >= today:
            continue
        day = store["days"].setdefault(d, blank_day())
        before = list(day.get("market", []))
        day["market"] = [round(sum(hrs[h]) / len(hrs[h]), 1) if len(hrs.get(h, [])) >= 6 else None for h in range(24)]
        recompute(day)
        changed += before != day["market"]
    save(store, "market", f"{changed}일 갱신")
    print(f">> [총수요-시장수요] {changed}일 갱신, 보유 {len(store['days'])}일")


# ── 2) PPA·BTM: 현재값 1건 → 해당 시간대 표본 ─────────────────────────────
def run_snapshot():
    store = load()
    data = http_json(SNAPSHOT_API, {"serviceKey": API_KEY, "pageNo": 1, "numOfRows": 10, "dataType": "json"})
    items = items_of(data) if data else []
    rec = None
    for x in items:
        dt = dt_of(x)
        if len(dt) >= 12:
            rec = (dt, num(x, "pEsmw"), num(x, "bEmsw"))
            break
    if not rec:
        save(store, "snapshot", "현재값 미수신", ok=False)
        sys.exit(">> [총수요-PPA·BTM] 현재값을 받지 못했습니다")
    dt, ppa, btm = rec
    d, h = dt[:8], int(dt[8:10])
    day = store["days"].setdefault(d, blank_day())
    for k in ("ppa", "btm", "sample_time"):
        day.setdefault(k, [None] * 24)
    if day["sample_time"][h] is None:          # 시간대마다 첫 표본만 기록(중복 실행 시 덮어쓰지 않음)
        day["ppa"][h], day["btm"][h], day["sample_time"][h] = ppa, btm, dt[8:12]
        note = f"{d} {h:02d}시대 기록 (기준 {dt[8:12]}, PPA {ppa}, BTM {btm})"
    else:
        note = f"{d} {h:02d}시대는 이미 기록됨 (기존 기준 {day['sample_time'][h]}) — 변경 없음"
    recompute(day)
    save(store, "snapshot", note)
    print(f">> [총수요-PPA·BTM] {d} {h:02d}시대: PPA {ppa} / BTM {btm} MW (기준 {dt[8:12]})")


if __name__ == "__main__":
    if not API_KEY:
        sys.exit(">> [총수요] DATA_GO_KR_KEY 없음 — 건너뜀")
    run_snapshot() if "--snapshot" in sys.argv else run_market()
