"""
指标 ④ 之一: FINRA 散户保证金债务

数据源策略（按优先级）:
1. Trading Economics (best-effort) — 公开页面，JS 渲染
2. 本地缓存（data/manual/finra_margin.json）— 之前手工录入的
3. manual_input.py 手工录入

注：FINRA Margin Debt 本身是季频数据，每月更新意义不大
    但有 proxy 月频数据：FINRA Broker-Dealer Customer Margin
"""
import os
import re
import json
import logging
from datetime import date, datetime, timedelta
import requests
from bs4 import BeautifulSoup

import db

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

CACHE_FILE = "data/manual/finra_margin.json"
TRADING_ECON_URL = "https://tradingeconomics.com/united-states/margin-debt"


def _load_cache() -> list:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_cache(records: list):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def _fetch_trading_economics() -> list:
    """
    尝试从 Trading Economics 拉 margin debt
    返回: [{"period": "2025-09", "value_m": 123456.0, "source": "trading_economics"}]
    """
    try:
        r = requests.get(TRADING_ECON_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"Trading Economics fetch failed: {e}")
        return []

    # 解析 HTML
    soup = BeautifulSoup(r.text, "html.parser")
    # TE 的数据在 <table id="myTable"> 里
    table = soup.find("table", {"id": "myTable"}) or soup.find("table", class_="table")
    if not table:
        # 退而求其次：找所有形如 "2025-09-01 123456" 的模式
        text = r.text
        m = re.findall(r'(\d{4}-\d{2})-01[^0-9]+([\d,.]+)', text)
        if m:
            return [{"period": p, "value_m": float(v.replace(",", "")), "source": "trading_economics"} for p, v in m[:50]]
        return []

    records = []
    rows = table.find_all("tr")[1:]  # 跳过表头
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        period_text = cells[0].get_text(strip=True)
        value_text = cells[-1].get_text(strip=True).replace(",", "").replace(" ", "")
        try:
            value = float(value_text)
            # TE 单位通常是 Billions
            records.append({
                "period": period_text[:7],  # YYYY-MM
                "value_m": value * 1000,   # Billions -> Millions
                "source": "trading_economics",
            })
        except ValueError:
            continue
    return records


def fetch_retail_margin_growth() -> dict:
    """
    主入口：拉 margin debt + 计算 3 个月累增
    """
    # 1. 尝试从 Trading Economics 拉
    fresh = _fetch_trading_economics()
    cache = _load_cache()

    if fresh:
        # 合并去重（按 period）
        existing_periods = {r["period"] for r in cache}
        for r in fresh:
            if r["period"] not in existing_periods:
                cache.append(r)
        cache.sort(key=lambda x: x["period"])
        _save_cache(cache)
        records = cache
        source_note = "trading_economics"
    else:
        # 2. 用本地缓存
        if not cache:
            return {"error": "no margin debt data; please run manual_input.py finra-margin to add"}
        records = cache
        source_note = "local_cache"

    # 计算 3M 累增
    if len(records) < 2:
        return {"error": "need at least 2 quarters of data", "records": len(records)}

    latest = records[-1]
    prev = records[-2]
    growth = (latest["value_m"] - prev["value_m"]) / prev["value_m"] * 100

    # 写 DB
    obs_date = f"{latest['period']}-01"
    db.insert_data(
        "retail_margin_debt", latest["value_m"], obs_date=obs_date,
        source=source_note,
        raw_payload={"all_records": len(records)},
    )
    db.insert_data(
        "retail_margin_growth", growth, obs_date=obs_date,
        source=source_note,
        raw_payload={"current_m": latest["value_m"], "prev_m": prev["value_m"]},
    )
    return {
        "metric_key": "retail_margin_growth",
        "value": growth,
        "obs_date": obs_date,
        "current_m": latest["value_m"],
        "current_b": latest["value_m"] / 1000,
        "source": source_note,
        "history_points": len(records),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_retail_margin_growth())
