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


def build_day_payload(d_str, land_smp, prev_day_data, cap=None, smp_avg=None, avg_basis=None, gen_info=None):
    """cap: KPX 전력수급실적 {'cap','peak','time','res'}. 없으면 전일 값을 임시로 유지(로그로 경고).
    smp_avg: 가중평균 SMP(미지정 시 단순평균). gen_info: {'gen':{...}, 'note':str} (미지정 시 전일 발전량 유지)."""
    d_dt = datetime.strptime(d_str, "%Y%m%d").replace(tzinfo=KST)
    if smp_avg is None:
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

    payload = {
        'smp_hourly': land_smp, 'smp_avg': smp_avg, 'smp_max': smp_max, 'smp_min': smp_min,
        'smp_max_idx': max_idx, 'smp_min_idx': min_idx, 'peak_band': f"{left+1}~{right+1}시", 'peak_left': left, 'peak_right': right,
        'cap_cap': cap_cap, 'cap_peak': cap_peak, 'cap_time': cap_time, 'cap_res': cap_res,
        'lng_price': lng_price, 'diff_lng_text': diff_lng_text, 'diff_lng_color': diff_lng_color, 'prev_smp_str': prev_month_smp_str(d_str),
        'diff_avg_txt': diff_avg_txt, 'diff_avg_color': diff_avg_color,
        'diff_max_txt': diff_max_txt, 'diff_max_color': diff_max_color,
        'diff_min_txt': diff_min_txt, 'diff_min_color': diff_min_color,
        'recent_7': recent_7, 'gen': prev_day_data.get("gen", default_gen)
    }
    if avg_basis:
        payload['smp_basis'] = avg_basis
    if gen_info:
        payload['gen'] = gen_info['gen']
        payload['gen_src'] = gen_info.get('src', 'api')
        payload['gen_note'] = gen_info.get('note', '')
    return payload


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


LAND_MLFD = {}  # {일자: [24시간 육지 수요예측(MW)]}  ← SMP 가중평균 계산용


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

    by_hour, mlfd_by_hour = {}, {}
    for x in land:
        try:
            by_hour[int(x["hour"])] = float(x["smp"])
        except (KeyError, ValueError, TypeError):
            continue
        try:
            mlfd_by_hour[int(x["hour"])] = float(x["mlfd"])
        except (KeyError, ValueError, TypeError):
            pass
    hours = sorted(by_hour)
    if hours != list(range(1, 25)) and hours != list(range(0, 24)):
        print(f">> [시간 오류] 24시간이 모두 있지 않습니다. 수신 시간대: {hours}")
        return None
    if sorted(mlfd_by_hour) == hours and sum(mlfd_by_hour.values()) > 0:
        LAND_MLFD[trade_date] = [mlfd_by_hour[h] for h in hours]
    else:
        print(f">> [수요예측 경고] {trade_date} 수요예측(mlfd)이 모두 있지 않아 가중평균을 계산할 수 없습니다.")
    return [by_hour[h] for h in hours]


def smp_avg_for(d_str, smp):
    """KPX 공표 '가중평균'과 같은 방식: 시간대별 SMP를 육지 수요예측(MW)으로 가중평균 (2022~2026 공표값과 비교 시 0.01원 이내 일치).
    수요예측이 없으면 단순평균으로 대체하고 기준을 'mean'으로 표시."""
    w = LAND_MLFD.get(d_str)
    if w and len(w) == len(smp):
        return round(sum(p * q for p, q in zip(smp, w)) / sum(w), 2), "weighted"
    return round(sum(smp) / len(smp), 2), "mean"


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


# ---------------------------------------------------------------------------
# 발전원별 발전량: 한국전력거래소_발전원별 발전량(계통기준) — 5분 단위 자료를 시간대별 평균(MW)으로 변환
#   응답 필드(추정 매핑, 실행 시 기존 확정 데이터와 대조해 검증):
#   fuelPwr1 수력 | 2 유류 | 3 유연탄 | 4 원자력 | 5 양수(충전 시 음수) | 6 가스 | 7 국내탄 | 8 신재생(풍력+기타) | 9 태양광
#   ※ 이 API에는 풍력·ESS가 따로 없음 → 풍력은 '신재생-기타 신재생(검증일에서 산출)'로 추정, ESS는 0으로 표시
# ---------------------------------------------------------------------------
GEN_API_URL = "https://apis.data.go.kr/B552115/PwrAmountByGen/getPwrAmountByGen"
FUEL_KEYS = [f"fuelPwr{i}" for i in range(1, 10)]
GEN_SERIES = [("원자력", "nuclear"), ("석탄", "coal"), ("유류", "oil"), ("LNG", "gas"),
              ("수력", "hydro"), ("태양광", "solar")]


def _gen_items(data):
    root = data.get("response", data) if isinstance(data, dict) else {}
    header = root.get("header", {}) if isinstance(root, dict) else {}
    body = root.get("body", {}) if isinstance(root, dict) else {}
    items = body.get("items", {}) if isinstance(body, dict) else {}
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return header, body, (items if isinstance(items, list) else [])


def fetch_gen_rows(oldest_date, max_pages=30):
    """최신 자료부터 내려가며 oldest_date(YYYYMMDD) 이전 자료가 나올 때까지 수집.
    반환: {YYYYMMDD: {baseDatetime(14자리): [fuelPwr1..9]}}  (실패 시 {})"""
    print(f">> [발전량] 발전원별 발전량 API 조회 중 (최소 {oldest_date} 자료까지)...")
    rows, size, page = {}, None, 1
    for try_size in (1000, 300, 100):
        params = {"serviceKey": API_KEY, "pageNo": 1, "numOfRows": try_size, "dataType": "json"}
        got = http_get(GEN_API_URL, params, attempts=2)
        if got is None:
            print(">> [발전량 경고] 접속 실패")
            return {}
        try:
            header, body, items = _gen_items(json.loads(got[1]))
        except ValueError:
            print(">> [발전량 경고] JSON이 아님:", got[1][:300].replace(API_KEY, "***"))
            return {}
        print(f">> [발전량 결과] HTTP {got[0]} code={header.get('resultCode')} msg={header.get('resultMsg')} "
              f"totalCount={body.get('totalCount')} 요청행수={try_size} 수신행수={len(items)}")
        if items:
            size = try_size
            break
    if not size:
        print(">> [발전량 경고] 자료를 받지 못했습니다.")
        return {}

    def absorb(items):
        oldest_seen = None
        for x in items:
            try:
                dt = str(x["baseDatetime"])[:14]
                vals = [float(x[k]) for k in FUEL_KEYS]
            except (KeyError, ValueError, TypeError):
                continue
            rows.setdefault(dt[:8], {})[dt] = vals
            oldest_seen = dt[:8] if oldest_seen is None or dt[:8] < oldest_seen else oldest_seen
        return oldest_seen

    first_dt = str(items[0].get("baseDatetime", ""))[:14]
    last_dt = str(items[-1].get("baseDatetime", ""))[:14]
    print(f">> [발전량] 첫 행 {first_dt} / 마지막 행 {last_dt} (최신순이어야 함)")
    oldest_seen = absorb(items)
    while oldest_seen and oldest_seen >= oldest_date and page < max_pages:
        page += 1
        params = {"serviceKey": API_KEY, "pageNo": page, "numOfRows": size, "dataType": "json"}
        got = http_get(GEN_API_URL, params, attempts=2)
        if got is None:
            print(f">> [발전량 경고] {page}쪽 접속 실패")
            break
        try:
            _, _, items = _gen_items(json.loads(got[1]))
        except ValueError:
            break
        if not items:
            break
        oldest_seen = absorb(items)
    print(f">> [발전량] 수집 일자: {min(rows) if rows else '-'} ~ {max(rows) if rows else '-'} ({page}쪽)")
    return rows


def hourly_fuels(day_rows):
    """하루치 5분 자료 → F[k][h] (k=0..8, h=0..23 시간대별 평균). 시간대마다 6개 미만이면 불완전으로 보고 None."""
    buckets = [[] for _ in range(24)]
    for dt, v in day_rows.items():
        buckets[int(dt[8:10])].append(v)
    if any(len(b) < 6 for b in buckets):
        return None
    return [[sum(v[k] for v in buckets[h]) / len(buckets[h]) for h in range(24)] for k in range(9)]


def gen_from_hourly(F, wind_base):
    """시간대별 연료별 값 → 홈페이지 gen 구조 (기존 엑셀 기반 구조와 동일한 키/부호 규칙)."""
    nuc, oil, gas, hydro, pump, solar = F[3], F[1], F[5], F[0], F[4], F[8]
    coal = [a + b for a, b in zip(F[2], F[6])]
    p_gen = [max(0.0, v) for v in pump]
    p_load = [min(0.0, v) for v in pump]
    wind = [max(0.0, r - wind_base) for r in F[7]] if wind_base is not None else [0.0] * 24
    net = [nuc[i] + coal[i] + oil[i] + gas[i] + hydro[i] + p_gen[i] for i in range(24)]
    r2 = lambda xs: [round(v, 2) for v in xs]
    return {'nuclear': r2(nuc), 'coal': r2(coal), 'oil': r2(oil), 'gas': r2(gas), 'hydro': r2(hydro),
            'pump_gen': r2(p_gen), 'pump_load': r2(p_load), 'ess_dis': [0.0] * 24, 'ess_chg': [0.0] * 24,
            'wind': r2(wind), 'solar': r2(solar), 'net_load': r2(net), 'spread': r2(pump)}


def validate_gen_mapping(F, stored, label):
    """API 값을 기존 확정(엑셀) 값과 대조. 반환: (통과여부, 기타신재생 기준값 또는 None)"""
    conv = gen_from_hourly(F, None)
    ok = True
    print(f">> [발전량 검증] 기준일 {label}: API 시간대별 평균 vs 기존 확정값 (평균 절대차이, 허용오차 = max(80MW, 평균의 3%))")
    checks = [(n, conv[k], stored[k]) for n, k in GEN_SERIES]
    checks.append(("양수(순)", F[4], [a + b for a, b in zip(stored["pump_gen"], stored["pump_load"])]))
    for name, api, old in checks:
        mean_abs = sum(abs(v) for v in old) / 24
        diff = sum(abs(a - b) for a, b in zip(api, old)) / 24
        tol = max(80.0, 0.03 * mean_abs)
        good = diff <= tol
        ok = ok and good
        print(f"   - {name}: 평균 {mean_abs:,.0f}MW, 차이 {diff:,.1f}MW → {'OK' if good else '불일치'}")
    diffs = [F[7][h] - stored["wind"][h] for h in range(24)]
    m = sum(diffs) / 24
    sd = (sum((d - m) ** 2 for d in diffs) / 24) ** 0.5
    base = None
    if 500 <= m <= 5000 and sd <= 150:
        base = round(m, 1)
    print(f"   - 신재생(API)-풍력(기존): 평균 {m:,.0f}MW, 편차 {sd:,.0f}MW → {'기타 신재생 기준값으로 사용' if base is not None else '일정하지 않아 풍력 추정 생략(0)'}")
    return ok, base


def is_copied_gen(d):
    """해당 일자의 발전량이 전일 값을 그대로 복사한 것인지 (복사된 날 = API로 교체 대상)."""
    day = master["days"].get(d, {})
    prev = master["days"].get((datetime.strptime(d, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d"))
    g, pg = day.get("gen"), (prev or {}).get("gen")
    return bool(g and pg and g.get("nuclear") == pg.get("nuclear") and g.get("coal") == pg.get("coal"))


def build_gen_updates(need_days, vday):
    """{일자: {'gen':..., 'note':...}} 를 반환. 검증을 통과하지 못하면 {} (기존 값 유지)."""
    try:
        oldest = min(list(need_days) + ([vday] if vday else []))
        rows = fetch_gen_rows(oldest)
        if not rows:
            return {}
        calib = master.get("gen_calib") or {}
        wind_base = None
        if vday:
            F = hourly_fuels(rows.get(vday, {})) if vday in rows else None
            if F is None:
                print(f">> [발전량 경고] 검증일 {vday} 자료가 부족하여 검증하지 못했습니다. 발전량은 기존 값을 유지합니다.")
                return {}
            ok, wind_base = validate_gen_mapping(F, master["days"][vday]["gen"], vday)
            if not ok:
                print(">> [발전량 경고] 기존 확정값과 맞지 않아 발전량은 기존 값을 유지합니다. (위 표를 확인)")
                return {}
            master["gen_calib"] = {"ok": True, "validated_on": vday, "other_re_mw": wind_base}
        elif calib.get("ok"):
            wind_base = calib.get("other_re_mw")
            print(f">> [발전량] 비교할 기존 확정일이 없어 이전 검증 결과({calib.get('validated_on')})를 사용합니다.")
        else:
            print(">> [발전량 경고] 검증 기준이 없어 발전량은 기존 값을 유지합니다.")
            return {}
        note = "풍력=신재생-기타 신재생 추정, ESS 미제공(0)" if wind_base is not None else "풍력·ESS 미제공(0)"
        out = {}
        for d in sorted(need_days):
            F = hourly_fuels(rows.get(d, {})) if d in rows else None
            if F is None:
                got = len(rows.get(d, {}))
                print(f">> [발전량 경고] {d} 자료가 24시간 모두 확보되지 않았습니다 (5분 자료 {got}건). 기존 값을 유지합니다.")
                continue
            out[d] = {"gen": gen_from_hourly(F, wind_base), "note": note}
            g = out[d]["gen"]
            print(f">> [발전량] {d} 반영: 원자력 {sum(g['nuclear'])/24:,.0f} / 석탄 {sum(g['coal'])/24:,.0f} / LNG {sum(g['gas'])/24:,.0f} "
                  f"/ 태양광 {sum(g['solar'])/24:,.0f} / 풍력(추정) {sum(g['wind'])/24:,.0f} MW (일평균)")
        return out
    except Exception as e:
        print(f">> [발전량 오류] {type(e).__name__}: {e} — 발전량은 기존 값을 유지합니다.")
        return {}


# ---------------------------------------------------------------------------
# 발전원별 발전량(1순위): KPX '발전원별 실시간 전력수급' 페이지 (과거 엑셀과 같은 출처 — 풍력·ESS·태양광 세부 포함)
#   날짜를 지정해 조회(POST)하면 그날의 5분 단위 자료(ictArr)가 페이지에 들어 있음
#   windPower 풍력 | nuclearPower 원자력 | totCoal 석탄 | oil 유류 | gas 가스 | waterPower 수력
#   raisingWater 양수(충전 시 음수) | essMw ESS(충전 시 음수) | sunlight 태양광(전력시장)
# ---------------------------------------------------------------------------
KPX_SRC_PAGE = "https://new.kpx.or.kr/powerSource.es?mid=a10404030000&device=chart"
KPX_SRC_KEYS = ["nuclearPower", "totCoal", "oil", "gas", "waterPower", "raisingWater", "essMw", "windPower", "sunlight"]
_kpx_sess = {}


def fetch_kpx_source_day(ymd):
    """해당 일자 5분 자료 목록(ictArr). 실패 시 None."""
    try:
        if "s" not in _kpx_sess:
            s_ = requests.Session()
            r = s_.get(KPX_SRC_PAGE, headers={"User-Agent": "Mozilla/5.0"}, timeout=(20, 90))
            m = re.search(r'name="_csrf"\s+value="([^"]+)"', r.text)
            if r.status_code != 200 or not m:
                print(f">> [발전량(KPX) 경고] 페이지 접속/인증값 확인 실패 (HTTP {r.status_code})")
                return None
            _kpx_sess["s"], _kpx_sess["csrf"] = s_, m.group(1)
        dash = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        data = {"mid": "a10404030000", "device": "chart", "_csrf": _kpx_sess["csrf"],
                "view_sdate": dash, "view_edate": dash, "view_sdate31": dash, "view_edate31": dash}
        last = None
        for i in (1, 2):
            try:
                r = _kpx_sess["s"].post(KPX_SRC_PAGE, data=data, headers={"User-Agent": "Mozilla/5.0", "Referer": KPX_SRC_PAGE}, timeout=(20, 90))
                break
            except Exception as e:
                last = type(e).__name__
                print(f">> [발전량(KPX) 통신 실패 {i}/2] {last}")
                time.sleep(10)
        else:
            return None
        m = re.search(r"var\s+ictArr\s*=\s*(\[.*?\])\s*;", r.text, re.S)
        if r.status_code != 200 or not m:
            print(f">> [발전량(KPX) 경고] {ymd} 자료를 찾지 못했습니다 (HTTP {r.status_code})")
            return None
        arr = json.loads(m.group(1))
        arr = [x for x in arr if str(x.get("regDate", "")).startswith(dash)]
        print(f">> [발전량(KPX)] {ymd} 5분 자료 {len(arr)}건 수신")
        return arr
    except Exception as e:
        print(f">> [발전량(KPX) 오류] {ymd}: {type(e).__name__}: {e}")
        return None


def kpx_hourly(arr):
    """5분 자료 → {항목: 24시간 평균}. 시간대마다 6건 미만이면 None."""
    hrs = [[] for _ in range(24)]
    try:
        for x in arr:
            hrs[int(str(x["regDate"])[11:13])].append([float(str(x[k]).replace(",", "")) for k in KPX_SRC_KEYS])
    except (KeyError, ValueError, TypeError) as e:
        print(f">> [발전량(KPX) 경고] 자료 형식 오류: {type(e).__name__} {e}")
        return None
    if any(len(h) < 6 for h in hrs):
        return None
    return {k: [sum(v[i] for v in hrs[h]) / len(hrs[h]) for h in range(24)] for i, k in enumerate(KPX_SRC_KEYS)}


def gen_from_kpx(H):
    p, e = H["raisingWater"], H["essMw"]
    p_gen, p_load = [max(0.0, v) for v in p], [min(0.0, v) for v in p]
    e_dis, e_chg = [max(0.0, v) for v in e], [min(0.0, v) for v in e]
    nuc, coal, oil, gas, hydro = H["nuclearPower"], H["totCoal"], H["oil"], H["gas"], H["waterPower"]
    net = [nuc[i] + coal[i] + oil[i] + gas[i] + hydro[i] + p_gen[i] + e_dis[i] for i in range(24)]
    r2 = lambda xs: [round(v, 2) for v in xs]
    return {'nuclear': r2(nuc), 'coal': r2(coal), 'oil': r2(oil), 'gas': r2(gas), 'hydro': r2(hydro),
            'pump_gen': r2(p_gen), 'pump_load': r2(p_load), 'ess_dis': r2(e_dis), 'ess_chg': r2(e_chg),
            'wind': r2(H["windPower"]), 'solar': r2(H["sunlight"]), 'net_load': r2(net),
            'spread': r2([p[i] + e[i] for i in range(24)])}


def build_gen_updates_kpx(need_days, vday):
    """KPX 페이지 기반 발전량. 기존 확정일(vday)과 대조해 통과해야 사용. 실패한 날은 결과에서 빠짐(→ API로 대체)."""
    out = {}
    try:
        if vday:
            arr = fetch_kpx_source_day(vday)
            H = kpx_hourly(arr) if arr else None
            if H is None:
                print(f">> [발전량(KPX) 경고] 검증일 {vday} 자료를 확보하지 못했습니다. API 방식으로 대체합니다.")
                return {}
            conv, old = gen_from_kpx(H), master["days"][vday]["gen"]
            print(f">> [발전량(KPX) 검증] 기준일 {vday}: 페이지 값 vs 기존 확정값 (평균 절대차이, 허용오차 = max(30MW, 평균의 2%))")
            ok = True
            for name, key in [("원자력", "nuclear"), ("석탄", "coal"), ("유류", "oil"), ("LNG", "gas"), ("수력", "hydro"),
                              ("풍력", "wind"), ("태양광", "solar"), ("양수발전", "pump_gen"), ("양수펌핑", "pump_load"),
                              ("ESS방전", "ess_dis"), ("ESS충전", "ess_chg")]:
                mean_abs = sum(abs(v) for v in old[key]) / 24
                diff = sum(abs(a - b) for a, b in zip(conv[key], old[key])) / 24
                good = diff <= max(30.0, 0.02 * mean_abs)
                ok = ok and good
                print(f"   - {name}: 평균 {mean_abs:,.0f}MW, 차이 {diff:,.1f}MW → {'OK' if good else '불일치'}")
            if not ok:
                print(">> [발전량(KPX) 경고] 기존 확정값과 맞지 않아 API 방식으로 대체합니다.")
                return {}
        for d in sorted(need_days):
            arr = fetch_kpx_source_day(d)
            H = kpx_hourly(arr) if arr else None
            if H is None or sum(H["nuclearPower"]) / 24 < 1000:
                print(f">> [발전량(KPX) 경고] {d} 자료가 불완전합니다. API 방식으로 대체합니다.")
                continue
            g = gen_from_kpx(H)
            out[d] = {"gen": g, "src": "kpx", "note": "KPX 발전원별 실시간 전력수급(풍력·ESS 포함)"}
            print(f">> [발전량(KPX)] {d} 반영: 원자력 {sum(g['nuclear'])/24:,.0f} / 석탄 {sum(g['coal'])/24:,.0f} / LNG {sum(g['gas'])/24:,.0f} "
                  f"/ 풍력 {sum(g['wind'])/24:,.0f} / 태양광 {sum(g['solar'])/24:,.0f} MW (일평균)")
    except Exception as e:
        print(f">> [발전량(KPX) 오류] {type(e).__name__}: {e} — API 방식으로 대체합니다.")
    return out


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

target_avg, target_basis = smp_avg_for(target_date_str, api_smp)
print(f">> [SMP 평균] {target_date_str} 가중평균 {target_avg}원 (기준={target_basis}) / 단순평균 {round(sum(api_smp)/24, 2)}원")

# 3. 수집 성공 시 저장
# 3-1. 전일 전력수급실적(KPX)
kpx_rows = fetch_kpx_supply_demand()
cap_target = kpx_rows.get(target_date_str)
if not cap_target:
    print(f">> [전력수급 경고] {target_date_str} 실적이 표에서 확인되지 않아 최대전력·예비율은 전일 값을 임시로 유지합니다.")

# 3-2. 최근 10일 저장분 점검: (가) SMP 평균이 단순평균으로 들어간 날 (나) 전일 발전량이 복사된 날 (다) KPX 실적과 다른 날
win_start = (target_dt - timedelta(days=10)).strftime("%Y%m%d")
window = sorted(d for d in master["days"] if win_start <= d < target_date_str)


def smp_needs_fix(day):
    h = day.get("smp_hourly") or []
    return (len(h) == 24 and day.get("smp_basis") != "weighted"
            and abs(day.get("smp_avg", 0) - round(sum(h) / 24, 2)) <= 0.005)


fix_smp = {d for d in window if smp_needs_fix(master["days"][d])}
copied = {d for d in window if master["days"][d].get("gen_src") != "kpx"
          and (master["days"][d].get("gen_src") == "api" or is_copied_gen(d))}
vday_candidates = [d for d in window if d not in copied and not master["days"][d].get("gen_src")
                   and (master["days"][d].get("gen") or {}).get("wind")]
vday = vday_candidates[-1] if vday_candidates else None
need_gen = copied | {target_date_str}
gen_updates = build_gen_updates_kpx(need_gen, vday)
if need_gen - set(gen_updates):  # KPX 페이지에서 못 받은 날은 공공데이터포털 API 값으로 대체
    gen_updates.update(build_gen_updates(need_gen - set(gen_updates), vday))

fixed_avg = {}
for d in sorted(fix_smp):
    got = fetch_land_smp(d, attempts=2, use_curl_fallback=False)
    if got and d in LAND_MLFD:
        fixed_avg[d] = smp_avg_for(d, master["days"][d]["smp_hourly"])
        print(f">> [SMP 평균 보정] {d}: {master['days'][d]['smp_avg']} → {fixed_avg[d][0]}원 (수요예측 가중평균)")
    else:
        print(f">> [SMP 평균 경고] {d} 수요예측을 받지 못해 평균 보정을 건너뜁니다.")

for d in window:  # 날짜 오름차순: 앞선 날 보정이 뒤 날의 전일대비·최근7일에 반영되도록
    day = master["days"][d]
    k = kpx_rows.get(d)
    cap_same = (k is None or (day.get("cap_peak") == k["peak"] and day.get("cap_time") == k["time"]
                              and day.get("cap_res") == k["res"] and _day_cap(day) == k["cap"]))
    if d not in fixed_avg and d not in gen_updates and cap_same:
        continue
    if not cap_same:
        print(f">> [전력수급 보정] {d}: 최대전력 {day.get('cap_peak')}→{k['peak']}MW, 예비율 {day.get('cap_res')}→{k['res']}%")
    cap = k if k else {"cap": _day_cap(day), "peak": day.get("cap_peak"), "time": day.get("cap_time"), "res": day.get("cap_res")}
    avg, basis = fixed_avg.get(d, (day.get("smp_avg"), day.get("smp_basis")))
    master["days"][d] = build_day_payload(d, day["smp_hourly"], {"gen": day.get("gen", default_gen)}, cap=cap,
                                          smp_avg=avg, avg_basis=basis, gen_info=gen_updates.get(d))
    if d not in gen_updates and day.get("gen_src"):  # 이미 실제 자료로 채운 발전량 표시는 유지
        master["days"][d]["gen_src"], master["days"][d]["gen_note"] = day["gen_src"], day.get("gen_note", "")

prev_day = master["days"].get(prev_date_str, {})
master["days"][target_date_str] = build_day_payload(target_date_str, api_smp, prev_day, cap=cap_target,
                                                    smp_avg=target_avg, avg_basis=target_basis,
                                                    gen_info=gen_updates.get(target_date_str))
master["latest_date"] = target_date_str

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(master, f, ensure_ascii=False)

print(f"🎉 {target_date_str} SMP 반영 완료")
if cap_target:
    print(f">> [전력수급] {target_date_str} 공급능력 {cap_target['cap']:,}MW, 최대전력 {cap_target['peak']:,}MW({cap_target['time']}), 예비율 {cap_target['res']}% 반영")
if target_date_str in gen_updates:
    print(f">> [발전량] {target_date_str} 발전원별 발전량을 실제 자료로 반영했습니다 ({gen_updates[target_date_str]['note']}).")
else:
    print(f">> [발전량 경고] {target_date_str} 발전량은 API 반영에 실패하여 전일 값을 유지했습니다. 위 로그를 확인하세요.")
