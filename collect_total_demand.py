# -*- coding: utf-8 -*-
"""국가 총수요(시장수요 + PPA 추정 + BTM 추정) 시간대별 수집 → total_demand.json

출처: 공공데이터포털 '한국전력거래소_발전원별 발전량 현황조회' (build_report.py 발전량과 같은 API)
  - fuelPwrTot : 시장수요 (MW)
  - pEsmw      : PPA 추정 (MW)
  - bEmsw      : BTM 추정 (MW)   ※ 포털 표기 철자. bEsmw 등 다른 철자도 함께 확인
5분 단위 자료를 시간대별 평균(MW)으로 바꿔 저장. 하루 24개 값 × 4개 항목.
기존 total_demand.json은 지우지 않고 새 날짜만 덧붙이거나 갱신함(이력 누적).

필드 이름은 포털 문서 기준이며, 첫 실행 로그의 '응답 필드 목록'으로 실제 이름을 확인할 것.
"""
import json, os, time, sys
from datetime import datetime, timedelta, timezone

import requests

API_URL = "https://apis.data.go.kr/B552115/PwrAmountByGen/getPwrAmountByGen"
API_KEY = os.environ.get("DATA_GO_KR_KEY", "")
OUT = "total_demand.json"
KST = timezone(timedelta(hours=9))
LOOKBACK_DAYS = int(os.environ.get("DEMAND_LOOKBACK_DAYS", "10"))   # 최근 며칠까지 다시 받아 보정할지
MAX_PAGES = 40

FIELD_CANDIDATES = {
    "market": ["fuelPwrTot"],
    "ppa": ["pEsmw", "pEsMw", "ppaEsmw"],
    "btm": ["bEmsw", "bEsmw", "bEsMw", "btmEsmw"],
}


def http_json(params, attempts=3):
    for i in range(1, attempts + 1):
        try:
            r = requests.get(API_URL, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=(20, 60))
            if r.status_code == 200:
                return r.json()
            print(f">> [총수요] HTTP {r.status_code} ({i}/{attempts})")
        except Exception as e:
            print(f">> [총수요] 통신 실패 {type(e).__name__} ({i}/{attempts})")
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


def pick(x, keys):
    for k in keys:
        if k in x and x[k] not in (None, ""):
            try:
                return float(str(x[k]).replace(",", ""))
            except ValueError:
                return None
    return None


def fetch_rows(oldest):
    """최신 자료부터 oldest(YYYYMMDD) 이전 자료가 나올 때까지. 반환 {YYYYMMDD: {HH: [[market, ppa, btm], ...]}}"""
    rows, page, size, shown = {}, 1, 1000, False
    while page <= MAX_PAGES:
        data = http_json({"serviceKey": API_KEY, "pageNo": page, "numOfRows": size, "dataType": "json"})
        if data is None:
            print(f">> [총수요 경고] {page}쪽 수신 실패")
            break
        items = items_of(data)
        if not items:
            break
        if not shown:
            print(">> [총수요] 응답 필드 목록:", sorted(items[0].keys()))
            missing = [n for n, ks in FIELD_CANDIDATES.items() if not any(k in items[0] for k in ks)]
            if missing:
                print(f">> [총수요 경고] 응답에 없는 항목: {missing} — FIELD_CANDIDATES의 필드 이름을 확인하세요.")
            shown = True
        oldest_seen = None
        for x in items:
            dt = str(x.get("baseDatetime", ""))[:14]
            if len(dt) < 10:
                continue
            vals = [pick(x, FIELD_CANDIDATES[n]) for n in ("market", "ppa", "btm")]
            rows.setdefault(dt[:8], {}).setdefault(dt[8:10], []).append(vals)
            oldest_seen = dt[:8] if oldest_seen is None or dt[:8] < oldest_seen else oldest_seen
        if oldest_seen is None or oldest_seen < oldest:
            break
        page += 1
    return rows


def hourly(day):
    """시간대별 평균. 한 시간대에 5분 자료가 6건 미만이면 그 시간은 None."""
    out = {"market": [], "ppa": [], "btm": [], "total": []}
    for h in range(24):
        recs = day.get(f"{h:02d}", [])
        if len(recs) < 6:
            for k in out:
                out[k].append(None)
            continue
        avg = []
        for i in range(3):
            vs = [r[i] for r in recs if r[i] is not None]
            avg.append(round(sum(vs) / len(vs), 1) if len(vs) >= 6 else None)
        out["market"].append(avg[0]); out["ppa"].append(avg[1]); out["btm"].append(avg[2])
        out["total"].append(round(sum(avg), 1) if None not in avg else None)
    return out


def main():
    if not API_KEY:
        sys.exit(">> [총수요] DATA_GO_KR_KEY 없음 — 건너뜀")
    store = {"days": {}}
    if os.path.exists(OUT):
        store = json.load(open(OUT, encoding="utf-8"))
    today = datetime.now(KST).strftime("%Y%m%d")
    oldest = (datetime.now(KST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    rows = fetch_rows(oldest)
    if not rows:
        sys.exit(">> [총수요] 자료를 받지 못했습니다 — 기존 파일 유지")

    changed = 0
    for d in sorted(rows):
        if d < oldest or d >= today:          # 오늘(진행 중인 날)은 저장하지 않음
            continue
        h = hourly(rows[d])
        complete = all(v is not None for v in h["total"])
        rec = {**h, "complete": complete}
        if store["days"].get(d) != rec:
            store["days"][d] = rec
            changed += 1
    store["latest_date"] = max(store["days"]) if store["days"] else None
    store["meta"] = {
        "source": "공공데이터포털 한국전력거래소_발전원별 발전량 현황조회 (PwrAmountByGen)",
        "unit": "MW (5분 자료의 시간대별 평균)",
        "fields": {"market": "시장수요 fuelPwrTot", "ppa": "PPA 추정 pEsmw", "btm": "BTM 추정 bEmsw",
                   "total": "market + ppa + btm (국가 총수요, 산출)"},
        "hour_index": "배열 0번 = 00시대(00:00~00:55 자료 평균), 23번 = 23시대",
        "region": "API 설명상 육지·제주 포함 — 확인 필요",
        "complete": "24개 시간대 모두 5분 자료 6건 이상이면 true",
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
    }
    new = json.dumps(store, ensure_ascii=False, separators=(",", ":"))
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    if new != old:
        open(OUT, "w", encoding="utf-8").write(new)
    print(f">> [총수요] {changed}일 갱신, 보유 {len(store['days'])}일, 최신 {store['latest_date']}")


if __name__ == "__main__":
    main()
