"""
指标 ⑦: AI 上游利润占产业链利润比例

数据源: SEC EDGAR XBRL companyfacts API
- AI 上游: NVDA / AVGO / AMAT / LRCX / KLAC (5 家核心设备/设计厂)
- Mag 7: AAPL/MSFT/GOOGL/AMZN/META/NVDA/TSLA (含 NVDA 双重身份)
- 频率: 季度, TTM (trailing twelve months)

为什么这个指标:
- 报告图 19: 上游利润占比是 AI 是否处于泡沫顶部的关键信号
- 电新 2021 年顶时上游利润占比 ~45%
- AI 2024-起快速抬升, 当前 ~50%+

算法:
- 拆分每家公司 YTD 数据为单季 (Q1=3M, Q2=H1-Q1, Q3=9M-H1, Q4=FY-9M)
- 取每家最近 4 个连续单季加总 = TTM
- 上游 5 家 TTM 利润 / Mag 7 7 家 TTM 利润 = 上游占比
"""
import json
import time
import logging
from datetime import date, datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import db

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "AI Bubble Dashboard research@example.com",
    "Accept-Encoding": "gzip, deflate",
}
EDGAR_BASE = "https://data.sec.gov"

# CIK 来源: SEC EDGAR company_tickers.json
AI_UPSTREAM_CIKS = {
    "NVDA": "0001045810",  # NVIDIA - 设计
    "AVGO": "0001730168",  # Broadcom - 网络/芯片
    "AMAT": "0000006951",  # Applied Materials - 设备
    "LRCX": "0000707549",  # Lam Research - 设备
    "KLAC": "0000319201",  # KLA - 设备
}
MAG7_CIKS = {
    "AAPL":  "0000320193",
    "MSFT":  "0000789019",
    "GOOGL": "0001652044",
    "AMZN":  "0001018724",
    "META":  "0001326801",
    "NVDA":  "0001045810",  # 双重身份
    "TSLA":  "0001318605",
}
NETINCOME_TAGS = [
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLossAvailableToCommonStockholdersDiluted",
]


def _make_session() -> requests.Session:
    sess = requests.Session()
    retry = Retry(
        total=3, backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=5)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update(HEADERS)
    return sess


def _fetch_company_facts(cik: str, sess: requests.Session) -> dict | None:
    cik_padded = cik.zfill(10)
    url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik_padded}.json"
    try:
        r = sess.get(url, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"EDGAR companyfacts {cik} failed: {e}")
        return None


def _find_netincome(facts: dict) -> tuple | None:
    """找 netincome USD units, 返回 (tag, units)
    策略: 优先选 count 最多的 tag (覆盖时间最长)"""
    if not facts:
        return None
    candidates = []
    for ns, ns_facts in facts.get("facts", {}).items():
        for tag in NETINCOME_TAGS:
            if tag in ns_facts:
                units = ns_facts[tag].get("units", {}).get("USD", [])
                if units and len(units) >= 10:
                    candidates.append((tag, units))
    # 退化: 找所有 ProfitLoss/NIL 类的 tag
    for ns, ns_facts in facts.get("facts", {}).items():
        for tag in ns_facts:
            if any(k in tag for k in ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailable"]):
                units = ns_facts[tag].get("units", {}).get("USD", [])
                if units and len(units) >= 10:
                    candidates.append((tag, units))
    if not candidates:
        return None
    # 按 count 降序, 取最大的
    candidates.sort(key=lambda x: -len(x[1]))
    return candidates[0]


def _split_to_quarters(units: list) -> list:
    """
    把 YTD 数据拆成单季净利润
    算法: 对每条 6/9/12M record, 找前一条 3M record (end 差 ~90 天) 作为 Q1,
    Q2 = 6M - Q1, Q3 = 9M - 6M, Q4 = 12M - 9M

    dedup: 同一 (end, months) 取最大 abs(value)
    过滤: 10-K 只取 months=12 (FY), 10-Q 只取 months=3/6/9 (避免 comparative TTM 污染)
    """
    # Step 1: dedup 按 (end, months) 取最大 abs value
    deduped = {}
    for u in units:
        form = u.get("form")
        if form not in ("10-K", "10-Q"):
            continue
        try:
            start = u.get("start", "")
            end = u.get("end", "")
            if not start or not end:
                continue
            start_dt = datetime.strptime(start, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end, "%Y-%m-%d").date()
            months = round((end_dt - start_dt).days / 30.4)
            if months not in (3, 6, 9, 12):
                continue
            # 10-K 只取 12M, 10-Q 只取 3/6/9M (避免 comparative TTM 污染)
            if form == "10-K" and months != 12:
                continue
            if form == "10-Q" and months == 12:
                continue
            if end_dt > date.today():
                continue
            key = (end, months)
            prev = deduped.get(key)
            if prev is None or abs(u["val"]) > abs(prev["value"]):
                deduped[key] = {
                    "end": end,
                    "end_dt": end_dt,
                    "months": months,
                    "value": u["val"],
                }
        except Exception:
            continue

    # Step 2: 按 end 排序
    records = sorted(deduped.values(), key=lambda x: x["end_dt"])

    # Step 3: 按 end 聚合到 "fiscal period" (end_dt 对应的季度)
    # 同一 end_dt 可能有多 months, 找 max
    by_end = {}
    for r in records:
        e = r["end"]
        if e not in by_end or r["months"] > by_end[e]["months"]:
            by_end[e] = r

    # Step 4: 对每个 end, 找前一条 3M (差 60-120 天) 作为 Q1
    ends_sorted = sorted(by_end.keys())
    quarters = []
    used_3m = set()  # 避免重复使用 Q1
    for e in ends_sorted:
        r = by_end[e]
        e_dt = r["end_dt"]
        if r["months"] == 3:
            if e not in used_3m:
                quarters.append({"end": e, "value": r["value"]})
                used_3m.add(e)
        elif r["months"] == 6:
            # 找前一条 3M, end 在 [e_dt - 130, e_dt - 60] days
            q1 = None
            for prev_e in ends_sorted:
                if prev_e >= e:
                    break
                pr = by_end[prev_e]
                if pr["months"] != 3:
                    continue
                diff = (e_dt - pr["end_dt"]).days
                if 60 <= diff <= 130:
                    q1 = pr
                    break
            if q1:
                quarters.append({"end": e, "value": r["value"] - q1["value"]})
                used_3m.add(q1["end"])
        elif r["months"] == 9:
            # 找前一条 6M
            h1 = None
            for prev_e in ends_sorted:
                if prev_e >= e:
                    break
                pr = by_end[prev_e]
                if pr["months"] != 6:
                    continue
                diff = (e_dt - pr["end_dt"]).days
                if 60 <= diff <= 130:
                    h1 = pr
                    break
            if h1:
                quarters.append({"end": e, "value": r["value"] - h1["value"]})
        elif r["months"] == 12:
            # 找前一条 9M
            ytd9 = None
            for prev_e in ends_sorted:
                if prev_e >= e:
                    break
                pr = by_end[prev_e]
                if pr["months"] != 9:
                    continue
                diff = (e_dt - pr["end_dt"]).days
                if 60 <= diff <= 130:
                    ytd9 = pr
                    break
            if ytd9:
                quarters.append({"end": e, "value": r["value"] - ytd9["value"]})
            else:
                # 没 9M: 用 3M x 4 估算
                q1_list = []
                for prev_e in ends_sorted:
                    if prev_e >= e:
                        break
                    pr = by_end[prev_e]
                    if pr["months"] != 3:
                        continue
                    diff = (e_dt - pr["end_dt"]).days
                    if 60 <= diff <= 130:
                        q1_list.append(pr)
                    if len(q1_list) == 1:
                        break
                if q1_list:
                    quarters.append({"end": e, "value": r["value"] - 4 * q1_list[0]["value"]})
    # 按 end 排序去重
    quarters.sort(key=lambda x: x["end"])
    seen = set()
    out = []
    for q in quarters:
        if q["end"] not in seen:
            out.append(q)
            seen.add(q["end"])
    return out


def _fetch_company_quarterly(cik: str, label: str) -> list:
    """拉单家公司拆分后的单季净利润 list"""
    sess = _make_session()
    facts = _fetch_company_facts(cik, sess)
    if not facts:
        return []
    result = _find_netincome(facts)
    if not result:
        logger.warning(f"{label} ({cik}): no netincome tag")
        return []
    tag, units = result
    quarters = _split_to_quarters(units)
    quarters.sort(key=lambda x: x["end"])
    logger.info(f"{label} ({cik}): {len(quarters)} quarterly records (tag={tag})")
    return quarters


def _calc_ttm(quarters: list, as_of: str) -> float | None:
    """算 as_of 时点的 TTM (最近 4 个单季, 每个 end <= as_of)"""
    valid = [q for q in quarters if q["end"] <= as_of]
    if len(valid) < 4:
        return None
    return sum(q["value"] for q in valid[-4:])


def _latest_calendar_quarter_end(today: date | None = None) -> str:
    """找最近的 calendar quarter end (<= today)"""
    today = today or date.today()
    for m, d in [(3, 31), (6, 30), (9, 30), (12, 31)]:
        try:
            qe = date(today.year, m, d)
            if qe <= today:
                return qe.strftime("%Y-%m-%d")
        except Exception:
            continue
    return f"{today.year - 1}-12-31"


def fetch_ai_upstream_profit() -> dict:
    """
    拉 AI 上游 (NVDA/AVGO/AMAT/LRCX/KLAC) + Mag 7 季度净利润
    计算 TTM 上游利润占比
    """
    upstream_quarterly = {}
    for label, cik in AI_UPSTREAM_CIKS.items():
        try:
            q = _fetch_company_quarterly(cik, label)
            if q:
                upstream_quarterly[label] = q
        except Exception as e:
            logger.warning(f"{label} fetch failed: {e}")
        time.sleep(0.3)

    mag7_quarterly = {}
    for label, cik in MAG7_CIKS.items():
        try:
            q = _fetch_company_quarterly(cik, label)
            if q:
                mag7_quarterly[label] = q
        except Exception as e:
            logger.warning(f"{label} fetch failed: {e}")
        time.sleep(0.3)

    if not upstream_quarterly or not mag7_quarterly:
        return {"error": "insufficient data from SEC EDGAR",
                "upstream": list(upstream_quarterly.keys()),
                "mag7": list(mag7_quarterly.keys())}

    as_of = _latest_calendar_quarter_end()
    logger.info(f"Computing TTM as of {as_of}")

    # 计算每家 TTM
    upstream_ttm = {}
    mag7_ttm = {}
    for label, qs in upstream_quarterly.items():
        ttm = _calc_ttm(qs, as_of)
        if ttm is not None:
            upstream_ttm[label] = ttm / 1e9
    for label, qs in mag7_quarterly.items():
        ttm = _calc_ttm(qs, as_of)
        if ttm is not None:
            mag7_ttm[label] = ttm / 1e9

    upstream_sum = sum(upstream_ttm.values()) * 1e9
    mag7_sum = sum(mag7_ttm.values()) * 1e9
    if upstream_sum == 0 or mag7_sum == 0:
        return {"error": "no TTM data",
                "as_of": as_of,
                "upstream_ttm_b": upstream_ttm,
                "mag7_ttm_b": mag7_ttm}

    ratio_pct = (upstream_sum / mag7_sum) * 100

    db.insert_data(
        "ai_upstream_profit", ratio_pct, obs_date=as_of,
        source="sec_edgar_ttm",
        raw_payload={
            "as_of": as_of,
            "upstream_sum_b": upstream_sum / 1e9,
            "mag7_sum_b": mag7_sum / 1e9,
            "upstream_breakdown_b": upstream_ttm,
            "mag7_breakdown_b": mag7_ttm,
        },
    )
    # 写每家公司 TTM 净利润到 DB
    for label, v in upstream_ttm.items():
        db.insert_data(
            f"ni_ttm_{label.lower()}", v, obs_date=as_of,
            source="sec_edgar_ttm",
            raw_payload={"kind": "upstream"},
        )
    for label, v in mag7_ttm.items():
        db.insert_data(
            f"ni_ttm_{label.lower()}", v, obs_date=as_of,
            source="sec_edgar_ttm",
            raw_payload={"kind": "mag7"},
        )

    return {
        "metric_key": "ai_upstream_profit",
        "value": ratio_pct,
        "obs_date": as_of,
        "upstream_sum_b": upstream_sum / 1e9,
        "mag7_sum_b": mag7_sum / 1e9,
        "upstream_breakdown_b": upstream_ttm,
        "mag7_breakdown_b": mag7_ttm,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print(json.dumps(fetch_ai_upstream_profit(), indent=2, ensure_ascii=False, default=str))