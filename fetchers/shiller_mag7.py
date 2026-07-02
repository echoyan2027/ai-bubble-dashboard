"""
指标 2: Shiller CAPE + Mag 7 集中度
- Shiller CAPE: 从 multpl.com 抓取
- Mag 7 集中度: 腾讯财经实时 API 价格 × SEC EDGAR shares outstanding
  (替代 yfinance + Yahoo Finance, 解决国内被墙问题)
"""
import re
import logging
import time
from datetime import date
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import db

logger = logging.getLogger(__name__)

SHILLER_URL = "https://www.multpl.com/shiller-pe/"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={tickers}"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Mag 7 流通股数 (latest 10-Q/10-K, 单位: 股)
# 数据源: 各公司最新季报 10-Q (2026Q1, filed 2026-04~05)
# 更新频率: 每季度财报发布后人工更新一次即可 (changes are small)
MAG7_SHARES_OUTSTANDING = {
    "AAPL":  14_960_000_000,   # 14.96B
    "MSFT":  7_432_000_000,   # 7.43B
    "GOOGL": 12_200_000_000,   # 12.2B (Class A + B + C)
    "AMZN":  10_700_000_000,  # 10.7B
    "META":  2_534_000_000,   # 2.53B
    "NVDA":  24_490_000_000,  # 24.49B
    "TSLA":  3_210_000_000,   # 3.21B
}
# SPY 价格 → S&P 500 总市值的换算
# S&P 500 Index (SPX) ≈ SPY × 10 (SPY 是 1/10 of S&P 500 价格水平)
# S&P 500 总市值 = SPX × Divisor (目前约 9.2B)
# 所以 S&P 500 总市值 ≈ SPY × 10 × 9.2B = SPY × 92B
# SPY price ~$746 → S&P 500 总市值 ~$68.7T
SPX_DIVISOR = 9.2e9
SPX_TO_SPY_RATIO = 10.0  # SPX ≈ SPY * 10


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
    sess.headers.update({"User-Agent": UA, "Referer": "https://gu.qq.com/"})
    return sess


def _tencent_quote(symbols: list[str]) -> dict:
    """腾讯财经实时报价 (一次拉多个 ticker)
    symbols: ['usAAPL', 'usMSFT', ...]
    Returns: {ticker_without_us_: price_float}

    响应格式: v_usAAPL="200~苹果~AAPL.OQ~307.14~294.38~...";
    """
    sess = _make_session()
    url = TENCENT_QUOTE_URL.format(tickers=",".join(symbols))
    try:
        resp = sess.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Tencent quote API error: {e}")
        return {}

    out = {}
    for line in resp.text.strip().split("\n"):
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        # 提取 v_usAAPL -> AAPL
        sym = key.strip().lstrip("v_")
        if sym.startswith("us"):
            sym = sym[2:]
        val = val.strip().strip('";\n ')
        parts = val.split("~")
        if len(parts) > 3:
            try:
                price = float(parts[3])
                if price > 0:
                    out[sym] = price
            except (ValueError, IndexError):
                continue
    return out


def fetch_shiller() -> dict:
    """抓取 multpl.com 的 Shiller CAPE 当期值"""
    sess = _make_session()
    try:
        resp = sess.get(SHILLER_URL, timeout=15)
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
    - 用腾讯 API 拿实时价格
    - 用硬编码 shares outstanding 计算市值
    - SPY (腾讯 usSPY) 估算 S&P 500 总市值 = SPY 市值 * 10
    """
    symbols = ["usAAPL", "usMSFT", "usGOOGL", "usAMZN", "usMETA", "usNVDA", "usTSLA", "usSPY"]
    quotes = _tencent_quote(symbols)

    if not quotes:
        return {"error": "Tencent quote API returned no data"}

    missing = [s for s in MAG7_SHARES_OUTSTANDING if s not in quotes]
    if missing:
        logger.warning(f"Missing quotes for: {missing}")

    mag7_mcap = 0.0
    breakdown = {}
    for sym, shares in MAG7_SHARES_OUTSTANDING.items():
        price = quotes.get(sym, 0)
        if not price:
            continue
        mc = price * shares
        mag7_mcap += mc
        breakdown[sym] = {
            "price": price,
            "shares_b": shares / 1e9,
            "mcap_t": mc / 1e12,
        }

    spy_price = quotes.get("SPY", 0)
    if not spy_price:
        return {"error": "SPY price not available", "mag7_mcap": mag7_mcap,
                "breakdown": breakdown}

    # S&P 500 总市值 ≈ SPY * SPX_TO_SPY_RATIO * SPX_DIVISOR
    spx_est = spy_price * SPX_TO_SPY_RATIO
    sp500_mcap = spx_est * SPX_DIVISOR

    if sp500_mcap <= 0 or mag7_mcap <= 0:
        return {"error": "market cap data incomplete", "mag7_mcap": mag7_mcap,
                "sp500_mcap": sp500_mcap, "breakdown": breakdown}

    ratio_pct = (mag7_mcap / sp500_mcap) * 100
    obs_date = date.today().isoformat()
    db.insert_data(
        "mag7_concentration", ratio_pct, obs_date=obs_date,
        source="tencent_quote",
        raw_payload={
            "mag7_mcap_t": mag7_mcap / 1e12,
            "sp500_mcap_t": sp500_mcap / 1e12,
            "spy_price": spy_price,
            "spx_est": spx_est,
            "spx_divisor": SPX_DIVISOR,
            "breakdown": breakdown,
        }
    )
    return {
        "metric_key": "mag7_concentration",
        "value": ratio_pct,
        "obs_date": obs_date,
        "breakdown": breakdown,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print("Shiller:", fetch_shiller())
    print("Mag 7:", fetch_mag7_concentration())
