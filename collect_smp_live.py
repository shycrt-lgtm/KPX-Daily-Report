"""실시간 SMP 화면용 자료 수집 — 한국전력거래소 '계통한계가격 및 수요예측'(공공데이터포털)
 전일·오늘 2일치 육지 SMP(24시간)와 수요예측을 받아 smp_live.json 에 저장한다.
 오늘 SMP는 전일 오후에 발표되므로 아침에는 항상 조회된다."""
import os, sys, json, time, requests
from datetime import datetime, timedelta, timezone

API_URL = "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand"
API_KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
OUT = "smp_live.json"
KST = timezone(timedelta(hours=9))

if not API_KEY:
    print("🚨 DATA_GO_KR_KEY 가 비어 있습니다. (GitHub Secret 확인)")
    sys.exit(1)

store = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
days = store.get("days", {})
now = datetime.now(KST)
want = [(now + timedelta(days=d)).strftime("%Y%m%d") for d in (-1, 0)]


def fetch(d):
    """반환: dict(smp, mlfd) | 'EMPTY'(아직 발표 전) | None(통신/형식 오류)"""
    params = {"serviceKey": API_KEY, "pageNo": 1, "numOfRows": 100, "dataType": "json", "date": d}
    for i in (1, 2, 3):
        try:
            r = requests.get(API_URL, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=(20, 60))
            data = r.json()
            root = data.get("response", data)
            head, body = root.get("header", {}), root.get("body", {})
            items = body.get("items", {}) if isinstance(body, dict) else {}
            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]
            print(f">> [호출] {d} HTTP {r.status_code} code={head.get('resultCode')} msg={head.get('resultMsg')} 건수={body.get('totalCount') if isinstance(body, dict) else None}")
            land = [x for x in (items or []) if "육지" in str(x.get("areaName", ""))]
            if not land:
                return "EMPTY"
            smp, mlfd = {}, {}
            for x in land:
                try:
                    smp[int(x["hour"])] = float(x["smp"])
                except (KeyError, ValueError, TypeError):
                    continue
                try:
                    mlfd[int(x["hour"])] = float(x["mlfd"])
                except (KeyError, ValueError, TypeError):
                    pass
            hrs = sorted(smp)
            if hrs not in (list(range(1, 25)), list(range(0, 24))):
                print(f">> [경고] {d} 24시간이 모두 있지 않습니다: {hrs}")
                return "EMPTY"
            return {"smp": [smp[h] for h in hrs],
                    "mlfd": [mlfd[h] for h in hrs] if sorted(mlfd) == hrs else None}
        except Exception as e:
            print(f">> [통신 실패 {i}/3] {d} {type(e).__name__}")
            time.sleep(10 * i)
    return None


ok_any = False
for d in want:
    res = fetch(d)
    if isinstance(res, dict):
        days[d] = {**res, "fetched": now.strftime("%Y-%m-%d %H:%M")}
        ok_any = True
    elif res == "EMPTY":
        days.pop(d, None)   # 발표 전(또는 자료 없음): 화면에서 '발표 전'으로 표시
        ok_any = True
    # None(통신 실패)이면 이전 저장값 유지

for k in list(days):
    if k not in want:
        del days[k]

store = {"updated": now.strftime("%Y-%m-%d %H:%M"), "days": days}
json.dump(store, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)

print(f">> [실시간 SMP] 저장 시각 {store['updated']}")
for d in want:
    v = days.get(d)
    if v:
        s = v["smp"]
        print(f"   {d}: 최고 {max(s):.2f} / 최저 {min(s):.2f} / 단순평균 {sum(s)/24:.2f}원")
    else:
        print(f"   {d}: 자료 없음 (발표 전이거나 조회 실패)")
if not ok_any:
    print("🚨 모든 조회가 실패했습니다. (smp_live.json 은 기존 내용 유지)")
    sys.exit(1)
