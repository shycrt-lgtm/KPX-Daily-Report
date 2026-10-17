# -*- coding: utf-8 -*-
"""전력거래소 홈페이지 '월별 SMP' 표 → kpx_monthly_smp.json
 - 표: 육지·제주·통합 SMP × (전년·올해) × 1~12월. 원/kWh
 - 새 월 값이 처음 나타난 날짜(first_seen, KST)를 기록해 공표 시점을 확인한다.
 - 값이 나중에 바뀌면 이전 값과 변경일을 history에 남긴다.
 - 접속·형식 오류 시 기존 파일을 바꾸지 않고 상태만 기록한다."""
import json, os, re, sys, time, urllib.request
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone

URL = "https://new.kpx.or.kr/smpMonthly.es?mid=a10406030000"
OUT = "kpx_monthly_smp.json"
KST = timezone(timedelta(hours=9))
LABELS = {"육지": "land", "제주": "jeju", "통합": "total"}


class Rows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self._r, self._c = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._r = []
        elif tag in ("td", "th") and self._r is not None:
            self._c = []

    def handle_data(self, data):
        if self._c is not None:
            self._c.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._c is not None and self._r is not None:
            self._r.append(re.sub(r"\s+", " ", "".join(self._c)).strip())
            self._c = None
        elif tag == "tr" and self._r is not None:
            self.rows.append(self._r)
            self._r = None


def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def parse(html):
    """return {YYYYMM: {"land":x, "jeju":y, "total":z}}  (행 머리글이 rowspan이라 연도 행만 있는 경우를 이어받음)"""
    p = Rows()
    p.feed(html)
    out, cur = {}, None
    for r in p.rows:
        if not r:
            continue
        yi = next((i for i, c in enumerate(r[:3]) if re.fullmatch(r"\d{2,4}\s*년", c)), None)
        if yi is None:
            continue
        for k, v in LABELS.items():
            if any(k in c for c in r[:yi]):
                cur = v
        if cur is None:
            continue
        y = int(re.match(r"\d+", r[yi]).group())
        y = y + 2000 if y < 100 else y
        for m, cell in enumerate(r[yi + 1: yi + 13], start=1):
            v = num(cell)
            if v is not None:
                out.setdefault(f"{y}{m:02d}", {})[cur] = v
    return out


def fetch():
    for i in (1, 2, 3):
        try:
            req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ko"})
            raw = urllib.request.urlopen(req, timeout=40).read()
            for enc in ("utf-8", "cp949"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    pass
        except Exception as e:
            print(f">> [월별SMP] 접속 실패 {type(e).__name__} ({i}/3)")
            time.sleep(10 * i)
    return None


def main():
    now = datetime.now(KST)
    store = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {"months": {}}
    html = fetch()
    got = parse(html) if html else {}
    store["status"] = {"at": now.strftime("%Y-%m-%d %H:%M KST"), "ok": bool(got),
                       "note": f"{len(got)}개월 읽음" if got else ("접속 실패" if html is None else "표 형식 인식 실패")}
    first_run = not store["months"]
    for ym, vals in got.items():
        rec = store["months"].get(ym)
        if rec is None:
            # 첫 실행 때 이미 있던 월은 공표일을 알 수 없으므로 null
            store["months"][ym] = {**vals, "first_seen": None if first_run else now.strftime("%Y-%m-%d"),
                                   "history": []}
            if not first_run:
                print(f">> [월별SMP] {ym} 신규 공표 확인: 육지 {vals.get('land')} (first_seen {now:%Y-%m-%d})")
        else:
            changed = {k: v for k, v in vals.items() if rec.get(k) != v}
            if changed:
                rec["history"].append({"at": now.strftime("%Y-%m-%d"), "before": {k: rec.get(k) for k in changed}})
                rec.update(changed)
    store["source"] = URL
    store["meta"] = {"unit": "원/kWh", "land": "육지 SMP", "jeju": "제주 SMP", "total": "통합 SMP",
                     "first_seen": "자동 수집으로 해당 월 값을 처음 확인한 날짜(KST). null = 수집 시작(2026-10-17) 전에 이미 게시됨",
                     "history": "게시 후 값이 바뀐 경우 이전 값과 변경 확인일"}
    store["latest_month"] = max(store["months"]) if store["months"] else None
    new = json.dumps(store, ensure_ascii=False, indent=1)
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    if new != old:
        open(OUT, "w", encoding="utf-8").write(new)
    print(f">> [월별SMP] {store['status']['note']}, 최신 {store['latest_month']}")
    if not got:
        sys.exit(1)


if __name__ == "__main__":
    main()
