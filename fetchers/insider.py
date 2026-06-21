"""
指标 ④ 之一: Mag 7 内部人 6M 卖出/买入比

数据源: OpenInsider.com
- 备选: SEC EDGAR Form 4 RSS（更原始，反爬更少）
"""
import re
import logging
from datetime import date, datetime, timedelta
import requests
from bs4 import BeautifulSoup

import db
from config import MAG7_TICKERS

logger = logging.getLogger(__name__)

OPENINSIDER_BASE = "http://openinsider.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _parse_insider_summary(ticker: str, months: int = 6) -> dict | None:
    """
    抓取 OpenInsider 的某 ticker 内部人交易汇总
    """
    url = f"{OPENINSIDER_BASE}/screener?s={ticker}&fd={months}&td=0&t=0&o=&cnt=1000"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"OpenInsider fetch failed for {ticker}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="tinytable")
    if not table:
        return None

    rows = table.find_all("tr")[1:]
    buy_value = 0.0
    sell_value = 0.0
    buy_count = 0
    sell_count = 0
    cutoff = datetime.now() - timedelta(days=months * 30)

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 12:
            continue
        try:
            trade_date = cells[1].get_text(strip=True)
            trade_date_obj = datetime.strptime(trade_date, "%Y-%m-%d")
            if trade_date_obj < cutoff:
                continue
            trade_type = cells[5].get_text(strip=True).lower()
            value_text = cells[11].get_text(strip=True).replace("$", "").replace(",", "")
            if "M" in value_text:
                value = float(value_text.replace("M", "")) * 1e6
            elif "K" in value_text:
                value = float(value_text.replace("K", "")) * 1e3
            else:
                value = float(value_text) if value_text else 0
            if "sell" in trade_type:
                sell_value += value
                sell_count += 1
            elif "buy" in trade_type:
                buy_value += value
                buy_count += 1
        except Exception:
            continue

    if buy_value == 0:
        return None
    return {
        "ticker": ticker,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_value": buy_value,
        "sell_value": sell_value,
        "sell_buy_ratio": sell_value / buy_value,
    }


def fetch_insider_sell_ratio() -> dict:
    """汇总 Mag 7 内部人 sell/buy 比例"""
    all_data = []
    for ticker in MAG7_TICKERS.keys():
        d = _parse_insider_summary(ticker, months=6)
        if d:
            all_data.append(d)

    if not all_data:
        return {"error": "no insider data"}

    total_sell = sum(d["sell_value"] for d in all_data)
    total_buy = sum(d["buy_value"] for d in all_data)
    ratio = total_sell / total_buy if total_buy > 0 else 0

    obs_date = date.today().isoformat()
    db.insert_data(
        "insider_sell_ratio", ratio, obs_date=obs_date,
        source="openinsider.com",
        raw_payload={"per_ticker": all_data, "total_sell": total_sell, "total_buy": total_buy},
    )
    return {
        "metric_key": "insider_sell_ratio",
        "value": ratio,
        "obs_date": obs_date,
        "details": all_data,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_insider_sell_ratio())
