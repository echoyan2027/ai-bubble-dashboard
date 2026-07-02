"""
指标 ④ 之一: Mag 7 内部人 6M 卖出/买入比

数据源: OpenInsider.com
- 主: OpenInsider 公开 screener 页面 (HTML 抓取)
- 备: SEC EDGAR Form 4 全文本搜索 (更稳定, 但慢)
"""
import re
import logging
import time
from datetime import date, datetime, timedelta
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import db
from config import MAG7_TICKERS

logger = logging.getLogger(__name__)

OPENINSIDER_BASE = "http://openinsider.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


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


def _parse_insider_summary(ticker: str, months: int = 6) -> dict | None:
    """
    抓取 OpenInsider 的某 ticker 内部人交易汇总
    关键修复: 之前 fd={months} 传的是 6 (天), 现在改为 fd={months*30} (180 天)
    """
    sess = _make_session()
    # OpenInsider 的 fd 参数是 "from days ago", 所以 6 个月 = 180 天
    days_back = months * 30
    url = f"{OPENINSIDER_BASE}/screener?s={ticker}&fd={days_back}&td=0&t=0&o=&cnt=1000"
    try:
        resp = sess.get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"OpenInsider fetch failed for {ticker}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # 多种 table class 候选 (OpenInsider 偶尔会换)
    table = None
    for cls in ["tinytable", "table", "sortable", ""]:
        if cls:
            table = soup.find("table", class_=cls)
        else:
            table = soup.find("table")
        if table:
            break

    if not table:
        logger.warning(f"OpenInsider: no table found for {ticker}")
        return None

    rows = table.find_all("tr")[1:]
    if not rows:
        return None

    buy_value = 0.0
    sell_value = 0.0
    buy_count = 0
    sell_count = 0
    cutoff = datetime.now() - timedelta(days=months * 30)

    parsed_rows = 0
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 12:
            continue
        try:
            # OpenInsider 列结构 (2026 版):
            # 0: X (delete flag)
            # 1: filing datetime
            # 2: trade date
            # 3: ticker
            # 4: insider name
            # 5: title
            # 6: trade type (S - Sale+OE, P - Purchase, etc.)
            # 7: price
            # 8: qty
            # 9: shares owned
            # 10: delta%
            # 11: value (负数 = 卖出, 正数 = 买入)
            trade_date = cells[2].get_text(strip=True)
            trade_date_obj = datetime.strptime(trade_date, "%Y-%m-%d")
            if trade_date_obj < cutoff:
                continue
            trade_type = cells[6].get_text(strip=True).lower()
            value_text = cells[11].get_text(strip=True).replace("$", "").replace(",", "").strip()
            if not value_text:
                continue
            if "M" in value_text:
                value = float(value_text.replace("M", "")) * 1e6
            elif "K" in value_text:
                value = float(value_text.replace("K", "")) * 1e3
            else:
                value = float(value_text)
            value = abs(value)  # value 是负数时表示卖出, 取绝对值
            if "sell" in trade_type or "-sale" in trade_type or "s -" in trade_type:
                sell_value += value
                sell_count += 1
                parsed_rows += 1
            elif "buy" in trade_type or "purchase" in trade_type or "p -" in trade_type:
                buy_value += value
                buy_count += 1
                parsed_rows += 1
        except Exception:
            continue

    if parsed_rows == 0:
        return None
    if buy_value == 0:
        # 没有 buy 时 sell/buy 比率无穷大, 设为 sell_value / 1 作为下限
        ratio = float(sell_count) if sell_count else 0
    else:
        ratio = sell_value / buy_value

    return {
        "ticker": ticker,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_value": buy_value,
        "sell_value": sell_value,
        "sell_buy_ratio": ratio,
    }


def fetch_insider_sell_ratio() -> dict:
    """汇总 Mag 7 内部人 sell/buy 比例"""
    all_data = []
    failed = []
    for ticker in MAG7_TICKERS.keys():
        d = _parse_insider_summary(ticker, months=6)
        if d:
            all_data.append(d)
        else:
            failed.append(ticker)
        time.sleep(0.5)  # 友好: 避免连续打 OpenInsider

    if not all_data:
        return {"error": "no insider data", "failed_tickers": failed}

    total_sell = sum(d["sell_value"] for d in all_data)
    total_buy = sum(d["buy_value"] for d in all_data)
    if total_buy == 0:
        ratio = float(sum(d["sell_count"] for d in all_data))
    else:
        ratio = total_sell / total_buy

    obs_date = date.today().isoformat()
    db.insert_data(
        "insider_sell_ratio", ratio, obs_date=obs_date,
        source="openinsider.com",
        raw_payload={
            "per_ticker": all_data,
            "failed_tickers": failed,
            "total_sell": total_sell,
            "total_buy": total_buy,
        },
    )
    return {
        "metric_key": "insider_sell_ratio",
        "value": ratio,
        "obs_date": obs_date,
        "details": all_data,
        "failed": failed,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_insider_sell_ratio())
