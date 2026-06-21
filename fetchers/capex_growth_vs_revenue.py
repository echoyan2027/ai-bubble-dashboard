"""
指标 ⑤: Capex 增速 vs 收入增速

派生指标 — 基于指标 ① 的 capex_revenue 季度数据计算
"""
import json
import os
import logging
from datetime import date, datetime

import db

logger = logging.getLogger(__name__)

QUARTERLY_FILE = "data/manual/capex_revenue_quarterly.json"


def fetch_capex_vs_revenue_growth() -> dict:
    """
    计算 Mag 4 capex 同比增速 / Mag 4 收入同比增速

    输入: data/manual/capex_revenue_quarterly.json (从指标 ① 生成)
    输出: 写入 capex_vs_revenue_growth 指标
    """
    if not os.path.exists(QUARTERLY_FILE):
        return {"error": "no quarterly data, please run capex_revenue first"}

    with open(QUARTERLY_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)

    if len(records) < 5:
        return {"error": "need at least 5 quarters of data to compute YoY growth",
                "records": len(records)}

    # 按 end 排序
    records.sort(key=lambda x: x["end"])
    latest = records[-1]

    # 找去年同期 (4 个季度前)
    latest_end = latest["end"]
    target_year = int(latest_end[:4]) - 1
    target_month = latest_end[5:7]
    target_end = f"{target_year}-{target_month}-{latest_end[8:]}"

    prior_year = None
    for r in records:
        if r["end"] == target_end:
            prior_year = r
            break

    if not prior_year:
        # Fallback: 找 4-5 季度前的数据
        if len(records) >= 5:
            prior_year = records[-5]
        else:
            return {"error": f"no prior year data for {target_end}"}

    capex_growth = (latest["hyperscaler_capex_b"] - prior_year["hyperscaler_capex_b"]) \
                   / prior_year["hyperscaler_capex_b"] * 100
    revenue_growth = (latest["total_revenue_b"] - prior_year["total_revenue_b"]) \
                     / prior_year["total_revenue_b"] * 100

    # 避免除零 / 极端值
    if abs(revenue_growth) < 1.0:
        # 收入同比变化 < 1% 时，比率不具有经济意义
        # 这种情况下用绝对差值代替：capex_growth - revenue_growth
        # 但仪表盘里标记为 "n/a"
        return {
            "metric_key": "capex_vs_revenue_growth",
            "value": None,
            "obs_date": latest["end"],
            "period": latest["period"],
            "capex_growth_pct": round(capex_growth, 2),
            "revenue_growth_pct": round(revenue_growth, 2),
            "note": f"revenue growth too small ({revenue_growth:.2f}%), ratio undefined",
        }

    ratio = capex_growth / revenue_growth
    # 截断到合理范围 (-10, 20)
    ratio = max(-10.0, min(20.0, ratio))

    obs_date = latest["end"]
    db.insert_data(
        "capex_vs_revenue_growth", ratio, obs_date=obs_date,
        obs_period=latest["period"],
        source="computed",
        raw_payload={
            "capex_growth_pct": capex_growth,
            "revenue_growth_pct": revenue_growth,
        },
    )
    return {
        "metric_key": "capex_vs_revenue_growth",
        "value": ratio,
        "obs_date": obs_date,
        "period": latest["period"],
        "capex_growth_pct": round(capex_growth, 2),
        "revenue_growth_pct": round(revenue_growth, 2),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_capex_vs_revenue_growth())
