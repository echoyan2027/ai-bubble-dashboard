"""
指标 ①: 四大 hyperscaler (MSFT/AMZN/GOOGL/META) 季度 Capex

数据源: SEC EDGAR XBRL
- 自动发现正确的 XBRL tag (us-gaap namespace，per-company facts API)
- 计算逻辑: 单季 capex (Q1/Q2/Q3/Q4 分别 3 个月)
- 输出: 4 家 hyperscaler 单季 capex 合计 + 估算的 AI 业务收入比

口径说明:
- Mag 4 capex 取自 10-Q / 10-K 中 PaymentsToAcquirePropertyPlantAndEquipment
  (AMZN 2017 后改用 PaymentsToAcquireProductiveAssets)
- "AI 业务收入" 估算口径: Mag 4 总收入 × 13%
  13% 来自 Menlo 2025 / 拾象报告对 hyperscaler AI 业务收入占比的估计
  实际 AI 业务收入需各公司细分披露，本指标用代理变量
- 各公司财年不同 (MSFT 7-6月, AMZN/GOOGL/META 1-12月)
  仪表盘按"披露季度"对齐 (即 10-Q 截止日)，不做日历季度强行对齐
"""
import os
import re
import json
import time
import logging
from datetime import date, datetime
import requests

import db
from config import SEC_CIKS

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "AI Bubble Dashboard research@example.com",
    "Accept-Encoding": "gzip, deflate",
}
EDGAR_BASE = "https://data.sec.gov"

# Capex 候选 tag（按优先级尝试，XBRL 命名空间自动检测）
CAPEX_TAG_CANDIDATES = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsForCapitalImprovements",
    "PurchaseOfPropertyAndEquipment",
    "CapitalExpenditure",
    "CapitalExpendituresIncurred",
]

# Revenue 候选 tag (按优先级: ASC 606 后标准 → 旧标准)
REVENUE_TAG_CANDIDATES = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]

# 单季 capex 占比估算（行业研究公开口径）
# 注：这是基于公开报告的代理变量，AI 业务实际收入需各公司细分披露
AI_REVENUE_FRACTION = 0.13  # ~13%, 来自 Menlo 2025 / 拾象报告


def _fetch_company_facts(cik: str) -> dict | None:
    """拉某公司的所有 XBRL facts（用于找正确的 tag）"""
    url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"EDGAR companyfacts fetch failed for {cik}: {e}")
        return None


def _find_tag(facts: dict, candidates: list) -> tuple | None:
    """
    在 companyfacts 中找最新一年（2024+）有数据的 tag
    优先取 2024+ 有数据的 tag（避免被废弃 tag 干扰）
    """
    if not facts:
        return None
    # 第一轮：找 2024+ 有数据的
    for ns, ns_facts in facts.get("facts", {}).items():
        for tag in candidates:
            if tag in ns_facts:
                unit_data = ns_facts[tag].get("units", {}).get("USD", [])
                recent = [u for u in unit_data if u.get("end", "") >= "2024-01-01"]
                if recent:
                    return ns, tag, unit_data
    # 第二轮：fallback 到任意有数据的
    for ns, ns_facts in facts.get("facts", {}).items():
        for tag in candidates:
            if tag in ns_facts:
                unit_data = ns_facts[tag].get("units", {}).get("USD", [])
                if unit_data:
                    return ns, tag, unit_data
    return None


def _extract_quarterly_values(unit_data: list, form_filter: tuple = ("10-K", "10-Q")) -> list:
    """
    从 XBRL units 中提取季度/年度数据
    保留: 10-K FY (全年) + 10-Q Q1/Q2/Q3 (累计)
    """
    records = []
    for u in unit_data:
        end = u.get("end", "")
        start = u.get("start", "")
        form = u.get("form", "")
        fp = u.get("fp", "")
        val = u.get("val", 0)
        fy = u.get("fy")
        filed = u.get("filed", "")

        if not end or not val or val <= 0:
            continue
        if form not in form_filter:
            continue
        # 跳过 future
        try:
            end_dt = datetime.strptime(end, "%Y-%m-%d").date()
        except Exception:
            continue
        if end_dt > date.today():
            continue

        # 跳过 8-K 等事件型
        if form == "8-K":
            continue

        # 计算期间月数
        period_months = _months_between(start, end)
        if period_months not in (3, 6, 9, 12):
            continue

        if period_months == 3 and fp == "Q1":
            period_type = "Q1"
            single_quarter = val
        elif period_months == 6 and fp == "Q2":
            period_type = "H1"
            single_quarter = val  # YTD 6M
        elif period_months == 9 and fp == "Q3":
            period_type = "YTD9M"
            single_quarter = val  # YTD 9M
        elif period_months == 12 and fp == "FY":
            period_type = "FY"
            single_quarter = val
        else:
            # 跳过异常
            continue

        records.append({
            "end": end,
            "start": start,
            "period_months": period_months,
            "period_type": period_type,
            "value": val,
            "value_b": val / 1e9,
            "single_quarter_value": single_quarter,
            "form": form,
            "fp": fp,
            "fy": fy,
            "filed": filed,
        })
    return records


def _months_between(start: str, end: str) -> int:
    if not start or not end:
        return 0
    try:
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end, "%Y-%m-%d")
        return round((e - s).days / 30.4)
    except Exception:
        return 0


def _calc_quarterly_values(records: list) -> list:
    """
    把 YTD/FY 转换成单季 (Q1/Q2/Q3/Q4)
    - Q1 = 3M (直接)
    - Q2 = 6M - 3M (用同一公司同财年的 H1 - Q1)
    - Q3 = 9M - 6M
    - Q4 = FY - 9M
    """
    if not records:
        return []

    # 按 fy + end 排序
    by_fy = {}
    for r in records:
        fy_key = (r["fy"], r["end"][:4])
        by_fy.setdefault(fy_key, []).append(r)

    quarterly = []
    for fy_key, rs in by_fy.items():
        # 找 Q1, H1, YTD9M, FY
        q1 = next((r for r in rs if r["period_type"] == "Q1"), None)
        h1 = next((r for r in rs if r["period_type"] == "H1"), None)
        ytd9 = next((r for r in rs if r["period_type"] == "YTD9M"), None)
        fy = next((r for r in rs if r["period_type"] == "FY"), None)

        # Q1: 3M 单季
        if q1:
            quarterly.append({
                "end": q1["end"],
                "quarter_label": f"Q1-{q1['end'][:4]}",
                "value": q1["value"],
                "value_b": q1["value"] / 1e9,
                "form": q1["form"],
            })
        # Q2: 6M - 3M
        if h1 and q1:
            q2_val = h1["value"] - q1["value"]
            quarterly.append({
                "end": h1["end"],
                "quarter_label": f"Q2-{h1['end'][:4]}",
                "value": q2_val,
                "value_b": q2_val / 1e9,
                "form": h1["form"],
            })
        # Q3: 9M - 6M
        if ytd9 and h1:
            q3_val = ytd9["value"] - h1["value"]
            quarterly.append({
                "end": ytd9["end"],
                "quarter_label": f"Q3-{ytd9['end'][:4]}",
                "value": q3_val,
                "value_b": q3_val / 1e9,
                "form": ytd9["form"],
            })
        # Q4: FY - 9M
        if fy and ytd9:
            q4_val = fy["value"] - ytd9["value"]
            quarterly.append({
                "end": fy["end"],
                "quarter_label": f"Q4-{fy['end'][:4]}",
                "value": q4_val,
                "value_b": q4_val / 1e9,
                "form": fy["form"],
            })

    return quarterly


def _fetch_ticker_quarterly(ticker: str) -> dict:
    """
    拉单家公司的 capex + revenue 季度数据
    """
    cik = SEC_CIKS[ticker]
    facts = _fetch_company_facts(cik)
    if not facts:
        return {"ticker": ticker, "error": "no facts"}

    time.sleep(0.2)

    # 找 capex tag
    capex_result = _find_tag(facts, CAPEX_TAG_CANDIDATES)
    if not capex_result:
        return {"ticker": ticker, "error": "no capex tag found"}
    capex_ns, capex_tag, capex_units = capex_result

    # 找 revenue tag
    rev_result = _find_tag(facts, REVENUE_TAG_CANDIDATES)
    if not rev_result:
        return {"ticker": ticker, "capex_tag": f"{capex_ns}:{capex_tag}",
                "warning": "no revenue tag found"}
    rev_ns, rev_tag, rev_units = rev_result

    # 提取并计算单季
    capex_records = _extract_quarterly_values(capex_units)
    capex_quarterly = _calc_quarterly_values(capex_records)

    rev_records = _extract_quarterly_values(rev_units)
    rev_quarterly = _calc_quarterly_values(rev_records)

    return {
        "ticker": ticker,
        "capex_tag": f"{capex_ns}:{capex_tag}",
        "rev_tag": f"{rev_ns}:{rev_tag}",
        "capex_quarterly": capex_quarterly,
        "rev_quarterly": rev_quarterly,
    }


def fetch_capex_revenue() -> dict:
    """
    主入口：拉 4 家 hyperscaler 季度 capex + revenue
    计算 Mag 4 季度合计 + 写入 DB
    """
    quarterly_file = "data/manual/capex_revenue_quarterly.json"
    os.makedirs(os.path.dirname(quarterly_file), exist_ok=True)

    mag4_quarterly = []
    summary = {}

    for ticker in ["MSFT", "AMZN", "GOOGL", "META"]:
        result = _fetch_ticker_quarterly(ticker)
        summary[ticker] = {
            "capex_tag": result.get("capex_tag"),
            "rev_tag": result.get("rev_tag"),
            "capex_points": len(result.get("capex_quarterly", [])),
            "rev_points": len(result.get("rev_quarterly", [])),
        }
        if "error" in result:
            logger.warning(f"{ticker}: {result['error']}")
            continue

        # 写入 DB (单家)
        for c in result["capex_quarterly"]:
            db.insert_data(
                f"capex_{ticker.lower()}", c["value_b"],
                obs_date=c["end"],
                obs_period=c["quarter_label"],
                source=f"sec_edgar_{result['capex_tag']}",
                raw_payload={"tag": result["capex_tag"]},
            )
        for r in result["rev_quarterly"]:
            db.insert_data(
                f"rev_{ticker.lower()}", r["value_b"],
                obs_date=r["end"],
                obs_period=r["quarter_label"],
                source=f"sec_edgar_{result['rev_tag']}",
                raw_payload={"tag": result["rev_tag"]},
            )

    # 算 Mag 4 季度合计
    # 统一按"披露季度 end 日期"对齐
    # 修复: 容忍 ≤4 家不齐 (例如 MSFT 2026Q1 SEC 还没同步)
    all_ends = set()
    for ticker in ["MSFT", "AMZN", "GOOGL", "META"]:
        ck = db.get_history(f"capex_{ticker.lower()}", limit=20)
        rk = db.get_history(f"rev_{ticker.lower()}", limit=20)
        for x in ck:
            all_ends.add(x["obs_date"])
        for x in rk:
            all_ends.add(x["obs_date"])

    quarterly_rows = []
    for end in sorted(all_ends, reverse=True):
        capex_sum = 0
        rev_sum = 0
        present = 0
        missing = []
        for ticker in ["MSFT", "AMZN", "GOOGL", "META"]:
            ck = db.get_history(f"capex_{ticker.lower()}", limit=20)
            rk = db.get_history(f"rev_{ticker.lower()}", limit=20)
            has_capex = False
            has_rev = False
            for x in ck:
                if x["obs_date"] == end:
                    capex_sum += x["value"]
                    present += 1
                    has_capex = True
                    break
            for x in rk:
                if x["obs_date"] == end:
                    rev_sum += x["value"]
                    has_rev = True
                    break
            if not (has_capex and has_rev):
                missing.append(ticker)

        # 关键修复: 至少 3 家有数据即可 (原来要 4 家)
        # 但要在 source 字段标记是否完整, 方便用户判断
        is_complete = (present == 4)
        if present < 3 or capex_sum == 0 or rev_sum == 0:
            continue

        ai_revenue = rev_sum * AI_REVENUE_FRACTION
        ratio = capex_sum / ai_revenue
        # 季度 label
        year = end[:4]
        month = int(end[5:7])
        quarter = (month - 1) // 3 + 1
        period_label = f"{year}Q{quarter}"

        # 部分数据时加 * 后缀
        if not is_complete:
            period_label = f"{period_label}*"

        source_tag = f"sec_edgar_auto"
        if not is_complete:
            source_tag = f"sec_edgar_auto_partial({present}/4, missing: {','.join(missing)})"

        quarterly_rows.append({
            "period": period_label,
            "end": end,
            "hyperscaler_capex_b": round(capex_sum, 2),
            "total_revenue_b": round(rev_sum, 2),
            "ai_revenue_b": round(ai_revenue, 2),
            "ratio": round(ratio, 4),
            "ai_revenue_fraction": AI_REVENUE_FRACTION,
            "source": source_tag,
            "added_at": date.today().isoformat(),
        })
        db.insert_data(
            "capex_revenue", ratio, obs_date=end, obs_period=period_label,
            source=source_tag,
            raw_payload={
                "capex_b": capex_sum, "ai_rev_b": ai_revenue, "ratio": ratio,
                "fraction": AI_REVENUE_FRACTION, "present": present, "missing": missing,
            },
        )

    if quarterly_rows:
        quarterly_rows.sort(key=lambda x: x["end"], reverse=True)
        with open(quarterly_file, "w", encoding="utf-8") as f:
            json.dump(quarterly_rows, f, ensure_ascii=False, indent=2)
        latest = quarterly_rows[0]
        return {
            "metric_key": "capex_revenue",
            "value": latest["ratio"],
            "period": latest["period"],
            "capex_b": latest["hyperscaler_capex_b"],
            "total_revenue_b": latest["total_revenue_b"],
            "ai_revenue_b": latest["ai_revenue_b"],
            "ai_revenue_fraction": AI_REVENUE_FRACTION,
            "summary": summary,
        }
    return {"error": "no quarterly rows from SEC EDGAR", "summary": summary}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = fetch_capex_revenue()
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
