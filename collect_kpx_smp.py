# -*- coding: utf-8 -*-
"""전력거래소 홈페이지(kpx.or.kr) '육지 SMP' 표 → smp_live.json
 - 표에는 최근 7일(오늘 포함) 시간별 SMP 24개와 가중평균이 있다. 내일 SMP는 하루전 18시 이후 공표되며 이 표에 가장 먼저 반영된다.
 - 공공데이터포털(collect_smp_live.py) 값이 있어도, 이 표에 있는 날짜는 이 표의 값으로 덮어쓴다(가중평균 wavg 포함).
 - 접속·형식 오류 시에는 아무것도 바꾸지 않고 종료(기존 값 유지)."""
import json, os, re, sys, time, urllib.request
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone

URL = "https://kpx.or.kr/smpInland.es?mid=a10404080100&device=pc"
OUT = "smp_live.json"
KST = timezone(timedelta(hours=9))
KEEP_DAYS = 12


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self._t, self._r, self._c = [], None, None, None
    def handle_starttag(self, tag, attrs):
        if tag == "table": self._t = []
        elif tag == "tr" and self._t is not None: self._r = []
        elif tag in ("td", "th") and self._r is not None: self._c = []
    def handle_data(self, data):
        if self._c is not None: self._c.append(data)
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._c is not None and self._r is not None:
            self._r.append(re.sub(r"\s+", " ", "".join(self._c)).strip()); self._c = None
        elif tag == "tr" and self._r is not None and self._t is not None:
            self._t.append(self._r); self._r = None
        elif tag == "table" and self._t is not None:
            self.tables.append(self._t); self._t = None


def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def parse(html, now):
    """return {YYYYMMDD: {"smp":[24], "wavg": float|None}}"""
    p = Tables(); p.feed(html)
    for t in p.tables:
        head = next((r for r in t if sum(1 for c in r if re.search(r"\d{1,2}\.\d{1,2}", c)) >= 3), None)
        if not head:
            continue
        dates = []
        for c in head:
            m = re.search(r"(\d{1,2})\.(\d{1,2})", c)
            if m:
                mo, da = int(m.group(1)), int(m.group(2))
                y = now.year
                if datetime(y, mo, da, tzinfo=KST) > now + timedelta(days=5):
                    y -= 1
                dates.append(f"{y}{mo:02d}{da:02d}")
        n = len(dates)
        hours, wavg = {}, [None] * n
        for r in t:
            if not r: continue
            lab = r[0].replace(" ", "")
            m = re.fullmatch(r"(\d{1,2})(h|시)", lab)
            vals = r[-n:] if len(r) >= n else []
            if m and vals:
                hours[int(m.group(1))] = [num(v) for v in vals]
            elif "가중" in lab and vals:
                wavg = [num(v) for v in vals]
        if sorted(hours) != list(range(1, 25)):
            continue
        out = {}
        for i, d in enumerate(dates):
            arr = [hours[h][i] for h in range(1, 25)]
            if all(v is not None for v in arr):
                out[d] = {"smp": arr, "wavg": wavg[i]}
        return out
    return {}


def fetch():
    last = None
    for i in (1, 2, 3):
        try:
            req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ko"})
            raw = urllib.request.urlopen(req, timeout=40).read()
            for enc in ("utf-8", "cp949"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    pass
            return raw.decode("utf-8", "ignore")
        except Exception as e:
            last = e
            print(f">> [KPX SMP 통신 실패 {i}/3] {type(e).__name__}: {str(e)[:100]}")
            time.sleep(5 * i)
    raise last


def main():
    now = datetime.now(KST)
    try:
        html = fetch()
    except Exception:
        print(">> [KPX SMP] 접속 실패 → 기존 값 유지"); return
    got = parse(html, now)
    if not got:
        print(f">> [KPX SMP] 표를 읽지 못했습니다(형식 변경 가능). 응답 {len(html)}자, 앞부분: {html[:150]!r}"); return
    store = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    days = store.get("days", {})
    for d, v in sorted(got.items()):
        old = days.get(d, {})
        days[d] = {"smp": v["smp"], "mlfd": old.get("mlfd") if old.get("smp") == v["smp"] else None,
                   "wavg": v["wavg"], "src": "kpx", "fetched": now.strftime("%Y-%m-%d %H:%M")}
        s = v["smp"]
        print(f">> [KPX SMP] {d}: 최고 {max(s)} / 최저 {min(s)} / 가중평균 {v['wavg']}")
    for k in sorted(days)[:-KEEP_DAYS]:
        del days[k]
    store = {"updated": now.strftime("%Y-%m-%d %H:%M"), "days": days}
    json.dump(store, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f">> [KPX SMP] {len(got)}일 반영 ({min(got)}~{max(got)})")


if __name__ == "__main__":
    main()
