"""
历史数据回填（用于回测）

从公开数据源拉取指标的历史月度/季度值，写入 DB
数据源：
- Yale Shiller Data: 1928+ 月度 P/E、CAPE、S&P 价格
- Trading Economics: FINRA Margin Debt 季频历史
- yfinance: 标普 500 历史

这些数据用于：
1. 5 个 DIY 指标中 Shiller CAPE / Mag 7 集中度 / Margin Debt 的历史回填
2. 历史回测：计算 0-100 指数 1990+ 走势
"""
import os
import re
import json
import time
import logging
from datetime import date, datetime, timedelta
import requests
import yfinance as yf

import db

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

YALE_XLS_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
CACHE_DIR = "data/historical"
os.makedirs(CACHE_DIR, exist_ok=True)


def _download_yale_xls() -> str:
    """下载 Yale Shiller 数据 (1.6MB xls) 到本地"""
    cache_path = os.path.join(CACHE_DIR, "ie_data.xls")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 100000:
        return cache_path
    try:
        r = requests.get(YALE_XLS_URL, headers=HEADERS, timeout=60)
        r.raise_for_status()
        with open(cache_path, "wb") as f:
            f.write(r.content)
        logger.info(f"Downloaded Yale xls: {len(r.content)/1024:.1f} KB")
        return cache_path
    except Exception as e:
        logger.error(f"Yale xls download failed: {e}")
        return None


def _parse_yale_xls(xls_path: str) -> list:
    """
    解析 Yale xls — 用 xlrd 1.2.0 原生 API
    实际格式（确认列）:
    - col 0: Date (1871.01)
    - col 1: S&P Comp. P (S&P 500 nominal price)
    - col 2: Dividend
    - col 3: Earnings
    - col 4: CPI
    - col 5: Date Fraction
    - col 6: Long Interest Rate (GS10)
    - col 7: Real Price
    - col 8: Real Dividend
    - col 9: Real Total Return Price
    - col 10: Real Earnings
    - col 11: Real TR Scaled Earnings
    - col 12: P/E10 or CAPE  ← 关键！
    """
    try:
        import xlrd
    except ImportError:
        logger.error("xlrd not installed. pip install xlrd==1.2.0")
        return []

    try:
        wb = xlrd.open_workbook(xls_path, formatting_info=False)
    except Exception as e:
        logger.error(f"xlrd open failed: {e}")
        return []

    sheet = None
    for name in wb.sheet_names():
        sh = wb.sheet_by_name(name)
        if sh.nrows > 100:
            sheet = sh
            break
    if sheet is None:
        sheet = wb.sheet_by_index(0)

    logger.info(f"Yale xls: sheet={sheet.name}, nrows={sheet.nrows}, ncols={sheet.ncols}")

    # 找数据起始行（找第一个 cell 是 1871.x 之类的）
    start_row = None
    for r in range(min(20, sheet.nrows)):
        v = sheet.cell_value(r, 0)
        if isinstance(v, float) and 1800 < v < 2100 and v != int(v):
            start_row = r
            break
    if start_row is None:
        # 兜底：找表头 "Date" 后的第一行
        for r in range(min(20, sheet.nrows)):
            v = sheet.cell_value(r, 0)
            if isinstance(v, str) and v.strip().lower() == "date":
                start_row = r + 1
                break
    if start_row is None:
        start_row = 7  # Yale 实际表头在 row 7
    logger.info(f"Data starts at row {start_row}")

    records = []
    for r in range(start_row, sheet.nrows):
        try:
            v_date = sheet.cell_value(r, 0)
            v_sp = sheet.cell_value(r, 1) if sheet.ncols > 1 else None
            v_cape = sheet.cell_value(r, 12) if sheet.ncols > 12 else None  # CAPE 在 col 12
            v_pe = sheet.cell_value(r, 10) if sheet.ncols > 10 else None  # Real E ratio

            if v_date is None or v_cape is None:
                continue
            # 跳过 "NA" / 空
            if isinstance(v_cape, str):
                if v_cape.strip().upper() in ("NA", "N/A", ""):
                    continue
                try:
                    v_cape = float(v_cape)
                except ValueError:
                    continue

            if not isinstance(v_cape, (int, float)) or v_cape <= 0:
                continue

            # 解析日期
            if isinstance(v_date, float):
                year = int(v_date)
                month_frac = v_date - year
                month = int(round(month_frac * 100))
            elif isinstance(v_date, (int,)):
                year = v_date
                month = 1
            else:
                continue

            if not (1 <= month <= 12):
                continue

            cape = float(v_cape)
            if cape > 200:  # 合理性检查：CAPE 2000 峰值 44，>200 异常
                continue

            sp = float(v_sp) if v_sp and not isinstance(v_sp, str) and v_sp > 0 else None
            pe = float(v_pe) if v_pe and not isinstance(v_pe, str) and v_pe > 0 else None

            records.append({
                "date": f"{year:04d}-{month:02d}-01",
                "sp500": sp,
                "cape": cape,
                "pe": pe,
            })
        except Exception:
            continue
    return records


def backfill_shiller_history() -> dict:
    """
    从 Yale xls 拉 Shiller CAPE 1990+ 历史
    """
    xls = _download_yale_xls()
    if not xls:
        return {"error": "yale xls download failed"}

    records = _parse_yale_xls(xls)
    if not records:
        return {"error": "no records parsed from yale xls"}

    # 写 DB
    cnt = 0
    for r in records:
        db.insert_data(
            "shiller_cape", r["cape"], obs_date=r["date"],
            source="yale_shiller",
            raw_payload={"sp500": r["sp500"]},
        )
        cnt += 1
    return {
        "metric_key": "shiller_cape",
        "rows": cnt,
        "first": records[0]["date"],
        "last": records[-1]["date"],
    }


def backfill_sp500_history() -> dict:
    """从 yfinance 拉标普 500 月末价格"""
    try:
        sp500 = yf.Ticker("^GSPC")
        hist = sp500.history(period="max", interval="1mo")
    except Exception as e:
        logger.error(f"yfinance SP500 failed: {e}")
        return {"error": str(e)}

    if hist is None or hist.empty:
        return {"error": "no SP500 data"}

    cnt = 0
    for idx, row in hist.iterrows():
        obs_date = idx.strftime("%Y-%m-%d")
        db.insert_data(
            "sp500_price", float(row["Close"]), obs_date=obs_date,
            source="yfinance",
            raw_payload={"open": float(row.get("Open", 0))},
        )
        cnt += 1
    return {"metric_key": "sp500_price", "rows": cnt}


def backfill_finra_history() -> dict:
    """
    从 Trading Economics 拉 FINRA Margin Debt 历史
    若 TE 抓不到，用本地缓存
    """
    cache_file = "data/manual/finra_margin.json"
    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if cache:
            cnt = 0
            for r in cache:
                obs = f"{r['period']}-01"
                db.insert_data(
                    "retail_margin_debt", r["value_m"], obs_date=obs,
                    source="manual_cache",
                    raw_payload={"period": r["period"]},
                )
                cnt += 1
            return {"metric_key": "retail_margin_debt", "rows": cnt, "source": "local_cache"}

    # 否则尝试拉 TE
    try:
        from fetchers.insider_margin import _fetch_trading_economics
        records = _fetch_trading_economics()
    except Exception:
        records = []

    if not records:
        return {"error": "no FINRA data; please use manual_input.py finra-margin to add"}

    cnt = 0
    for r in records:
        obs = f"{r['period']}-01"
        db.insert_data(
            "retail_margin_debt", r["value_m"], obs_date=obs,
            source="trading_economics",
            raw_payload={"period": r["period"]},
        )
        cnt += 1
    return {"metric_key": "retail_margin_debt", "rows": cnt, "source": "trading_economics"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== Shiller ===")
    print(backfill_shiller_history())
    print("\n=== SP500 ===")
    print(backfill_sp500_history())
    print("\n=== FINRA ===")
    print(backfill_finra_history())
