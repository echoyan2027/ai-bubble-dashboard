"""
指标 2: Shiller CAPE + Mag 7 集中度
- Shiller CAPE: 从 multpl.com 抓取
- Mag 7 集中度: 用 yfinance 拉 7 只股票市值 + ^GSPC 总市值
"""
import re
import json
import logging
from datetime import date
import requests
from bs4 import BeautifulSoup
import yfinance as yf

import db

logger = logging.getLogger(__name__)

SHILLER_URL = "https://www.multpl.com/shiller-pe/"


def fetch_shiller() -> dict:
    """
    抓取 multpl.com 的 Shiller CAPE 当期值

    Returns:
        {"metric_key": "shiller_cape", "value": float, "obs_date": "YYYY-MM-DD"}
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(SHILLER_URL, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to fetch Shiller: {e}")
        return {"error": str(e)}

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text()
    m = re.search(r"(\d+\.\d+)", text)
    if not m:
        return {"error": "value not found in page"}

    value = float(m.group(1))
    obs_date = date.today().isoformat()
    db.insert_data("shiller_cape", value, obs_date=obs_date,
                   source="multpl.com", raw_payload={"url": SHILLER_URL, "value": value})
    return {"metric_key": "shiller_cape", "value": value, "obs_date": obs_date}


def fetch_mag7_concentration() -> dict:
    """
    Mag 7 市值 / S&P 500 总市值
    - 拉 AAPL/MSFT/GOOGL/AMZN/META/NVDA/TSLA 的 marketCap
    - 拉 ^GSPC 的 marketCap（yfinance 已支持）
    """
    from config import MAG7_TICKERS
    all_tickers = list(MAG7_TICKERS.keys()) + ["^GSPC"]
    try:
        data = yf.Tickers(" ".join(all_tickers))
    except Exception as e:
        logger.error(f"yfinance Tickers error: {e}")
        return {"error": str(e)}

    mag7_mcap = 0.0
    breakdown = {}
    for tk in MAG7_TICKERS.keys():
        try:
            info = data.tickers[tk].fast_info
            mc = getattr(info, "market_cap", None) or 0
            mag7_mcap += mc
            breakdown[tk] = mc / 1e12  # 万亿
        except Exception as e:
            logger.warning(f"Failed to get market cap for {tk}: {e}")

    sp500_mcap = 0.0
    try:
        sp500_mcap = data.tickers["^GSPC"].fast_info.market_cap or 0
    except Exception as e:
        logger.warning(f"Failed to get S&P 500 market cap: {e}")

    if sp500_mcap <= 0 or mag7_mcap <= 0:
        return {"error": "market cap data incomplete", "mag7_mcap": mag7_mcap,
                "sp500_mcap": sp500_mcap, "breakdown": breakdown}

    ratio_pct = (mag7_mcap / sp500_mcap) * 100
    obs_date = date.today().isoformat()
    db.insert_data(
        "mag7_concentration", ratio_pct, obs_date=obs_date,
        source="yfinance", raw_payload={
            "mag7_mcap_t": mag7_mcap / 1e12,
            "sp500_mcap_t": sp500_mcap / 1e12,
            "breakdown_t": breakdown,
        }
    )
    return {
        "metric_key": "mag7_concentration",
        "value": ratio_pct,
        "obs_date": obs_date,
        "breakdown_t": breakdown,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print("Shiller:", fetch_shiller())
    print("Mag 7:", fetch_mag7_concentration())
