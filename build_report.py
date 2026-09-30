import os
import re
import json
import subprocess
import time
import urllib.parse
from html.parser import HTMLParser
import requests
import sys
from datetime import datetime, timedelta, timezone

print(">> SPA master data.json 일일 업데이트 시작...")

DATA_FILE = "data.json"

# 인증키는 코드에 적지 않고 GitHub Secret(DATA_GO_KR_KEY)에서만 읽음 (공개 저장소이므로 코드에 키 기재 금지)
API_KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
if not API_KEY:
    print("🚨 DATA_GO_KR_KEY 가 비어 있습니다. GitHub 저장소 Settings > Secrets and variables > Actions 에 등록하세요.")
    sys.exit(1)

if not os.path.exists(DATA_FILE):
    raise FileNotFoundError("data.json 파일이 없습니다.")

with open(DATA_FILE, "r", encoding="utf-8") as f:
    master = json.load(f)

KST = timezone(timedelta(hours=9))
target_dt = datetime.now(KST) - timedelta(days=1)
target_date_str = target_dt.strftime("%Y%m%d")
weekday_kr_list = ["월", "화", "수", "목", "금", "토", "일"]

default_gen = {
    'nuclear': [0]*24, 'coal': [0]*24, 'oil': [0]*24, 'gas': [0]*24, 'hydro': [0]*24,
    'pump_gen': [0]*24, 'pump_load': [0]*24, 'ess_dis': [0]*24, 'ess_chg': [0]*24,
    'wind': [0]*24, 'solar': [0]*24, 'net_load': [0]*24, 'spread': [0]*24
}


# ---------------------------------------------------------------------------
# 공통: HTTP 호출 (접속 시간 초과 등은 대기 후 재시도, 마지막에 curl(IPv4)로 대체 시도)
# ---------------------------------------------------------------------------
def _decode(b):
    for enc in ("utf-8", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", errors="replace")


def http_get(url, params=None, attempts=3, use_curl_fallback=True, timeout=(20, 60)):
    """(status, text) 를 반환하며 끝내 실패하면 None."""
    last = None
    for i in range(1, attempts + 1):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
            if r.status_code < 500 and r.status_code != 429:
                return r.status_code, _decode(r.content)
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}"
        print(f">> [통신 실패 {i}/{attempts}] {last}")
        if i < attempts:
            time.sleep(10 * i)

    if use_curl_fallback:
        print(">> [대체 호출] curl(IPv4)로 재시도합니다.")
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            res = subprocess.run(
                ["curl", "-sS", "-4", "--connect-timeout", "20", "--max-time", "60",
                 "-H", "User-Agent: Mozilla/5.0", "-w", "\n%{http_code}", full],
                capture_output=True, timeout=90)
            if res.returncode == 0 and res.stdout:
                body, _, code = res.stdout.rpartition(b"\n")
                return int(code or 0), _decode(body)
            print(f">> [curl 실패] exit={res.returncode} {_decode(res.stderr)[:200].replace(API_KEY, '***')}")
        except Exception as e:
            print(f">> [curl 에러] {type(e).__name__}")
    return None


# ---------------------------------------------------------------------------
# LNG 단가: 한국가스공사(KOGAS) 발전용 천연가스 요금 중 '일반발전사업자' 합계(원료비+공급비)
#   원/GJ → 원/㎥ 환산 (10,190kcal/㎥, 1kcal=4.1868J)  ※ 기존 엑셀(입방당/GJ당)과 동일 방식
# ---------------------------------------------------------------------------
KOGAS_URL = "https://www.kogas.or.kr/site/koGas/1040402000000"
GJ_PER_M3 = 10190 * 4.1868 / 1e6
LNG_COLUMN_NAME = "일반발전사업자"


class _TableParser(HTMLParser):
    """표(table)를 행/셀 텍스트 목록으로 추출 (표준 라이브러리만 사용)."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self._t = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._t = []
        elif tag == "tr" and self._t is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._t is not None:
            self._t.append(self._row)
            self._row = None
        elif tag == "table" and self._t is not None:
            self.tables.append(self._t)
            self._t = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_kogas_power_price(html):
    """페이지에서 (적용연월, 원료비, 공급비, 합계[원/GJ]) 중 '일반발전사업자' 열 값을 추출. 실패 시 None."""
    p = _TableParser()
    p.feed(html)
    for tbl in p.tables:
        flat = " ".join(" ".join(r) for r in tbl)
        if LNG_COLUMN_NAME not in flat or "합계" not in flat:
            continue
        ym, hdr, vals = None, None, {}
        for row in tbl:
            joined = " ".join(row)
            if ym is None:
                m = re.search(r"(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})", joined)
                if m and "시행" in joined:
                    ym = f"{int(m.group(1)):04d}{int(m.group(2)):02d}"
            if hdr is None and LNG_COLUMN_NAME in row:
                hdr = [c for c in row if c and c != "구분"]
            if row and row[0] in ("원료비", "공급비", "합계"):
                vals[row[0]] = row[1:]
        if not (ym and hdr and all(k in vals for k in ("원료비", "공급비", "합계"))):
            print(f">> [LNG 파싱 실패] 표 구조를 인식하지 못했습니다 (적용월={ym}, 머리글={hdr}, 항목={list(vals)})")
            continue
        if hdr.count(LNG_COLUMN_NAME) > 1:
            print(f">> [LNG 경고] '{LNG_COLUMN_NAME}' 열이 여러 개입니다. 첫 번째 열을 사용합니다: {hdr}")
        col = hdr.index(LNG_COLUMN_NAME)
        try:
            def num(key):
                return float(re.sub(r"[^0-9.]", "", vals[key][col]))
            fuel, supply, total = num("원료비"), num("공급비"), num("합계")
        except (IndexError, ValueError):
            print(f">> [LNG 파싱 실패] 숫자를 읽지 못했습니다 (머리글={hdr}, 값={vals})")
            continue
        if abs(fuel + supply - total) > 0.06 or not (5000 < total < 100000):
            print(f">> [LNG 검증 실패] 값이 비정상입니다: 원료비={fuel}, 공급비={supply}, 합계={total}")
            continue
        return {"ym": ym, "fuel_gj": fuel, "supply_gj": supply, "total_gj": total}
    return None


def lng_table():
    return master.setdefault("lng_monthly", {})


def seed_lng_table():
    """기존 일별 데이터의 월별 LNG 단가로 월별표를 채움(없는 달만)."""
    t = lng_table()
    for k, d in master["days"].items():
        ym = k[:6]
        if ym not in t and "lng_price" in d:
            t[ym] = {"total_m3": d["lng_price"], "src": "seed"}


def update_lng_from_kogas():
    """KOGAS 페이지의 현재 게시 월 단가를 월별표에 반영. 실패해도 전체 작업은 계속(직전 값 유지)."""
    print(">> [LNG 단가] KOGAS 발전용 천연가스 요금 조회 중...")
    got = http_get(KOGAS_URL, attempts=2)
    if got is None:
        print(">> [LNG 경고] KOGAS 페이지에 접속하지 못했습니다. 월별표의 기존 값을 사용합니다.")
        return
    status, text = got
    print(f">> [LNG 상태] HTTP {status}")
    info = parse_kogas_power_price(text)
    if not info:
        print(">> [LNG 경고] 요금 표를 읽지 못했습니다. 월별표의 기존 값을 사용합니다.")
        return
    ym = info["ym"]
    total_m3 = round(info["total_gj"] * GJ_PER_M3, 2)
    old = lng_table().get(ym)
    if old and abs(old.get("total_m3", total_m3) - total_m3) > 0.05:
        print(f">> [LNG 주의] {ym} 기존값 {old.get('total_m3')}원/㎥ → KOGAS 환산값 {total_m3}원/㎥ 로 갱신")
    lng_table()[ym] = {
        "total_m3": total_m3,
        "fuel_m3": round(info["fuel_gj"] * GJ_PER_M3, 2),
        "supply_m3": round(info["supply_gj"] * GJ_PER_M3, 2),
        "total_gj": info["total_gj"],
        "src": "kogas",
    }
    print(f">> [LNG 단가] {ym} {LNG_COLUMN_NAME} 합계 {info['total_gj']:,.2f}원/GJ → {total_m3:,.2f}원/㎥ (10,190kcal/㎥ 환산)")


def lng_price_for(ym):
    t = lng_table()
    if ym in t:
        return t[ym]["total_m3"]
    earlier = sorted(k for k in t if k < ym)
    if earlier:
        print(f">> [LNG 경고] {ym} 단가가 없어 {earlier[-1]} 값을 대신 사용합니다.")
        return t[earlier[-1]]["total_m3"]
    return None


def _prev_ym(ym):
    first = datetime.strptime(ym + "01", "%Y%m%d")
    return (first - timedelta(days=1)).strftime("%Y%m")


def lng_fields(d_str):
    """해당 일자가 속한 월의 LNG 단가와 전월비 문구/색상."""
    ym = d_str[:6]
    cur = lng_price_for(ym)
    if cur is None:
        print(f">> [LNG 경고] {ym} 단가 정보가 전혀 없어 0으로 표시합니다.")
        return 0.0, "전월비 변동없음", "text-slate-500"
    prev = lng_price_for(_prev_ym(ym))
    if prev is None:
        prev = cur
    d = round(cur - prev, 2)
    if d > 0:
        return cur, f"전월비 +{abs(d):.2f}원", "text-rose-600"
    if d < 0:
        return cur, f"전월비 ▼{abs(d):.2f}원", "text-blue-600"
    return cur, "전월비 변동없음", "text-slate-500"


def prev_month_smp_str(d_str):
    """전월 평균 SMP (전월 일평균 SMP의 평균)."""
    pym = _prev_ym(d_str[:6])
    vals = [d["smp_avg"] for k, d in master["days"].items() if k.startswith(pym) and "smp_avg" in d]
    return f"{sum(vals) / len(vals):.2f}원/kWh" if vals else "-"


# ---------------------------------------------------------------------------
# 전일 전력수급실적: 한국전력거래소(KPX) '전일 전력수급실적' 표 (공급능력·최대전력·발생시각·예비율)
# ---------------------------------------------------------------------------
KPX_URL = "https://kpx.or.kr/powerDemandPerform.es?mid=a10404060000"


def _num(s):
    return float(re.sub(r"[^0-9.\-]", "", s))


def parse_kpx_supply_demand(html):
    """표의 각 행을 {YYYYMMDD: {'cap','peak','time','res'}} 로 반환 (표에는 최근 10일이 표시됨).
    열 순서: 번호 | 일시 | 설비용량 | 공급능력 | 최대전력(전년·금년·증가율·시간대) | 최소전력(금년·시간대) | 공급예비력 | 예비율"""
    p = _TableParser()
    p.feed(html)
    out = {}
    for tbl in p.tables:
        for row in tbl:
            if len(row) < 12:
                continue
            m = re.fullmatch(r"(\d{4})\.(\d{2})\.(\d{2})", row[1])
            if not m:
                continue
            d = m.group(1) + m.group(2) + m.group(3)
            try:
                cap, peak, reserve, res = _num(row[3]), _num(row[5]), _num(row[10]), _num(row[11])
                hh = int(re.search(r"(\d{1,2})", row[7]).group(1))
            except (ValueError, AttributeError):
                print(f">> [전력수급 파싱 실패] {d} 행의 숫자를 읽지 못했습니다: {row}")
                continue
            # 열이 어긋났는지 검증: 공급능력-최대전력=공급예비력, 예비율=공급예비력/최대전력
            if abs((cap - peak) - reserve) > 1.5 or peak <= 0 or abs(res - reserve / peak * 100) > 0.2:
                print(f">> [전력수급 검증 실패] {d} 값이 서로 맞지 않아 제외합니다: 공급능력={cap}, 최대전력={peak}, 예비력={reserve}, 예비율={res}")
                continue
            out[d] = {"cap": int(cap), "peak": int(peak), "time": f"{hh:02d}:00", "res": res}
    return out


def fetch_kpx_supply_demand():
    print(">> [전력수급] KPX 전일 전력수급실적 조회 중...")
    got = http_get(KPX_URL, attempts=2)
    if got is None:
        print(">> [전력수급 경고] KPX 페이지에 접속하지 못했습니다.")
        return {}
    status, text = got
    print(f">> [전력수급 상태] HTTP {status}")
    rows = parse_kpx_supply_demand(text)
    if rows:
        print(f">> [전력수급] {len(rows)}일 확보 ({min(rows)}~{max(rows)})")
    else:
        print(">> [전력수급 경고] 표를 읽지 못했습니다.")
    return rows


def _day_cap(info):
    """일별 데이터의 공급능력(MW): 저장된 값 → 그날 recent_7 첫 항목 → 기본값."""
    if info.get("cap_cap"):
        return info["cap_cap"]
    r7 = info.get("recent_7") or [{}]
    return r7[0].get("cap") or 91000


def repair_days_with_kpx(kpx_rows, skip_date):
    """이미 저장된 날짜 중 KPX 실적과 다른 값(예: 전일 값이 복사된 날)을 실적으로 바로잡음."""
    for d in sorted(kpx_rows):
        day = master["days"].get(d)
        if not day or d == skip_date:
            continue
        k = kpx_rows[d]
        same = (day.get("cap_peak") == k["peak"] and day.get("cap_time") == k["time"]
                and day.get("cap_res") == k["res"] and _day_cap(day) == k["cap"])
        if same:
            continue
        print(f">> [전력수급 보정] {d}: 최대전력 {day.get('cap_peak')}→{k['peak']}MW, 예비율 {day.get('cap_res')}→{k['res']}%")
        master["days"][d] = build_day_payload(d, day["smp_hourly"], {"gen": day.get("gen", default_gen)}, cap=k)


def build_day_payload(d_str, land_smp, prev_day_data, cap=None):
    """cap: KPX 전력수급실적 {'cap','peak','time','res'}. 없으면 전일 값을 임시로 유지(로그로 경고)."""
    d_dt = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=KST)
    smp_avg = round(sum(land_smp)/24, 2)
    smp_max = round(max(land_smp), 2)
    smp_min = round(min(land_smp), 2)
    max_idx = land_smp.index(smp_max)
    min_idx = land_smp.index(smp_min)

    thresh = smp_min + (smp_max - smp_min) * 0.85
    left, right = max_idx, max_idx
    while left > 0 and land_smp[left-1] >= thresh: left -= 1
    while right < 23 and land_smp[right+1] >= thresh: right += 1
    if (right - left) > 6: left = max(0, max_idx - 2); right = min(23, max_idx + 2)

    if cap:
        cap_cap, cap_peak, cap_time, cap_res = cap["cap"], cap["peak"], cap["time"], cap["res"]
    else:
        cap_cap = _day_cap(prev_day_data)
        cap_peak = prev_day_data.get("cap_peak", 68420)
        cap_time = prev_day_data.get("cap_time", "19:00")
        cap_res = prev_day_data.get("cap_res", 36.8)

    recent_7 = [{
        'date': f"{d_dt.month}.{d_dt.day}({weekday_kr_list[d_dt.weekday()]})",
        'is_holiday': d_dt.weekday() in [5, 6] or d_str in master["holidays"],
        'avg': smp_avg, 'max': smp_max, 'min': smp_min,
        'cap': cap_cap, 'peak': cap_peak, 'time': cap_time, 'res': cap_res
    }]
    for delta in range(1, 7):
        p_dt = d_dt - timedelta(days=delta)
        p_k = p_dt.strftime("%Y%m%d")
        if p_k in master["days"]:
            p_info = master["days"][p_k]
            recent_7.append({
                'date': f"{p_dt.month}.{p_dt.day}({weekday_kr_list[p_dt.weekday()]})",
                'is_holiday': p_dt.weekday() in [5, 6] or p_k in master["holidays"],
                'avg': p_info["smp_avg"], 'max': p_info["smp_max"], 'min': p_info["smp_min"],
                'cap': _day_cap(p_info), 'peak': p_info["cap_peak"], 'time': p_info["cap_time"], 'res': p_info["cap_res"]
            })

    diff_avg_val = round(smp_avg - recent_7[1]["avg"], 2) if len(recent_7) > 1 else 0.0
    diff_max_val = round(smp_max - recent_7[1]["max"], 2) if len(recent_7) > 1 else 0.0
    diff_min_val = round(smp_min - recent_7[1]["min"], 2) if len(recent_7) > 1 else 0.0

    def fmt_diff(v):
        if v > 0: return f"+{abs(v):.2f}원", "text-rose-600"
        elif v < 0: return f"▼{abs(v):.2f}원", "text-blue-600"
        return "- 0.00원", "text-slate-500"

    diff_avg_txt, diff_avg_color = fmt_diff(diff_avg_val)
    diff_max_txt, diff_max_color = fmt_diff(diff_max_val)
    diff_min_txt, diff_min_color = fmt_diff(diff_min_val)

    lng_price, diff_lng_text, diff_lng_color = lng_fields(d_str)

    return {
        'smp_hourly': land_smp, 'smp_avg': smp_avg, 'smp_max': smp_max, 'smp_min': smp_min,
        'smp_max_idx': max_idx, 'smp_min_idx': min_idx, 'peak_band': f"{left+1}~{right+1}시", 'peak_left': left, 'peak_right': right,
        'cap_cap': cap_cap, 'cap_peak': cap_peak, 'cap_time': cap_time, 'cap_res': cap_res,
        'lng_price': lng_price, 'diff_lng_text': diff_lng_text, 'diff_lng_color': diff_lng_color, 'prev_smp_str': prev_month_smp_str(d_str),
        'diff_avg_txt': diff_avg_txt, 'diff_avg_color': diff_avg_color,
        'diff_max_txt': diff_max_txt, 'diff_max_color': diff_max_color,
        'diff_min_txt': diff_min_txt, 'diff_min_color': diff_min_color,
        'recent_7': recent_7, 'gen': prev_day_data.get("gen", default_gen)
    }


# 0. LNG 월별 단가표 준비 (기존 값으로 채운 뒤 KOGAS 최신 게시분 반영)
seed_lng_table()
update_lng_from_kogas()

# 1. 28일 완벽 복구
if "20260928" not in master["days"]:
    print(">> [복구] 28일 데이터 누락 감지, 공식 실적으로 즉시 생성합니다.")
    kpx_28 = [100.28, 97.45, 97.45, 97.45, 97.45, 97.45, 102.40, 107.07, 107.28, 107.24, 106.86, 107.23, 106.49, 107.23, 108.30, 112.47, 130.69, 130.69, 130.69, 130.69, 130.69, 130.69, 127.91, 115.49]
    prev_27 = master["days"].get("20260927", {})
    master["days"]["20260928"] = build_day_payload("20260928", kpx_28, prev_27)
    master["latest_date"] = "20260928"

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False)
    print(">> [복구] 28일 데이터 정상 디스크 기록 완료.")

# 2. 전일 SMP 수집 (한국전력거래소_계통한계가격 및 수요예측, 공공데이터포털)
API_URL = "https://apis.data.go.kr/B552115/SmpWithForecastDemand/getSmpWithForecastDemand"


def http_get_text(params, attempts=3, use_curl_fallback=True):
    return http_get(API_URL, params, attempts, use_curl_fallback)


def fetch_land_smp(trade_date, attempts=3, use_curl_fallback=True):
    """해당 일자 육지 SMP 24시간 리스트를 반환. 실패 시 None (원인은 로그에 출력)."""
    params = {"serviceKey": API_KEY, "pageNo": 1, "numOfRows": 100, "dataType": "json", "date": trade_date}
    print(f">> [API 호출] {trade_date} 육지 SMP 수집 중...")
    got = http_get_text(params, attempts, use_curl_fallback)
    if got is None:
        print(f">> [통신 에러] {trade_date} 호출이 모든 시도에서 실패했습니다.")
        return None
    status, text = got
    print(f">> [API 상태] HTTP {status}")
    try:
        data = json.loads(text)
    except ValueError:
        print(">> [응답 형식 오류] JSON이 아님:", text[:300].replace(API_KEY, "***"))
        return None

    root = data.get("response", data) if isinstance(data, dict) else {}
    header = root.get("header", {}) if isinstance(root, dict) else {}
    body = root.get("body", {}) if isinstance(root, dict) else {}
    print(f">> [API 결과] code={header.get('resultCode')} msg={header.get('resultMsg')} totalCount={body.get('totalCount')}")

    items = body.get("items", {}) if isinstance(body, dict) else {}
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        print(">> [데이터 없음] 응답 일부:", str(data)[:300].replace(API_KEY, "***"))
        return None

    areas = sorted({str(x.get("areaName", "")) for x in items})
    print(f">> [지역 구분] {areas}")
    land = [x for x in items if "육지" in str(x.get("areaName", ""))]
    if not land:
        print(">> [지역 오류] 육지 행을 찾지 못했습니다.")
        return None

    by_hour = {}
    for x in land:
        try:
            by_hour[int(x["hour"])] = float(x["smp"])
        except (KeyError, ValueError, TypeError):
            continue
    hours = sorted(by_hour)
    if hours != list(range(1, 25)) and hours != list(range(0, 24)):
        print(f">> [시간 오류] 24시간이 모두 있지 않습니다. 수신 시간대: {hours}")
        return None
    return [by_hour[h] for h in hours]


def report_check_vs_master(smp_list, d_str):
    """API 값이 기존 확정값(KPX 파일 기반)과 같은지 로그로만 확인 (실패 처리 안 함)."""
    old = master["days"].get(d_str, {}).get("smp_hourly")
    if not old or len(old) != 24:
        return
    diff = max(abs(a - b) for a, b in zip(smp_list, old))
    if diff < 0.01:
        print(f">> [검증] {d_str} API 값이 기존 확정값과 일치합니다.")
    else:
        print(f">> [검증 주의] {d_str} API 값이 기존 확정값과 다릅니다 (최대 차이 {diff:.2f}원). 하루 전 계획값일 수 있습니다.")


api_smp = fetch_land_smp(target_date_str)

if not api_smp:
    print(f"🚨 {target_date_str} SMP 수집 실패. 위 로그의 원인을 확인하세요. (data.json은 변경하지 않음)")
    sys.exit(1)

# 전일 값 검증(로그 전용)
prev_date_str = (target_dt - timedelta(days=1)).strftime("%Y%m%d")
prev_api = fetch_land_smp(prev_date_str, attempts=1, use_curl_fallback=False)
if prev_api:
    report_check_vs_master(prev_api, prev_date_str)
else:
    print(">> [검증 생략] 전일 비교용 호출이 실패하여 확정값 비교는 건너뜁니다.")

# 3. 수집 성공 시 저장
# 3-1. 전일 전력수급실적(KPX): 이미 저장된 날짜 중 실적과 다른 값 보정 → 대상일에 반영
kpx_rows = fetch_kpx_supply_demand()
cap_target = kpx_rows.get(target_date_str)
if not cap_target:
    print(f">> [전력수급 경고] {target_date_str} 실적이 표에서 확인되지 않아 최대전력·예비율은 전일 값을 임시로 유지합니다.")
repair_days_with_kpx(kpx_rows, skip_date=target_date_str)

prev_day = master["days"].get(prev_date_str, {})
master["days"][target_date_str] = build_day_payload(target_date_str, api_smp, prev_day, cap=cap_target)
master["latest_date"] = target_date_str

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(master, f, ensure_ascii=False)

print(f"🎉 {target_date_str} SMP 반영 완료")
if cap_target:
    print(f">> [전력수급] {target_date_str} 공급능력 {cap_target['cap']:,}MW, 최대전력 {cap_target['peak']:,}MW({cap_target['time']}), 예비율 {cap_target['res']}% 반영")
print(">> [참고] 발전원별 발전량은 아직 전일 값을 유지합니다 (다음 단계에서 API 연동).")
