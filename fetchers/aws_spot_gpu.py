"""
指标 ③: 半导体指数 3M 环比 — SOX / SMH ETF

数据源:
- 主要: yfinance ^SOX (PHLX Semiconductor Index)
- 备用: SOXX / SMH / PSI

计算:
- 月环比 = 本月收盘 / 上月收盘 - 1
- 3M 环比 = 本月 / 3月前 - 1 (更稳定，反映季度趋势)

为什么替换 AWS Spot GPU:
- AWS Spot Pricing 页面是 JS 渲染，无法直接抓
- RunPod / Paperspace 价格波动不直接反映需求疲软
- 半导体指数是最直接的"AI 算力需求热度"代理变量
  (NVDA/AMD/AVGO/ASML/MU 等都在 SOX 成分股里)

降级:
- yfinance 限流时，从本地缓存 data/manual/sox_history.json 读
"""
import os
import json
import logging
from datetime import date, datetime, timedelta
import yfinance as yf

import db

logger = logging.getLogger(__name__)

# 候选 ticker: 优先 ^SOX, 备用 SMH
TICKER_CANDIDATES = ["^SOX", "SOXX", "SMH", "PSI"]
CACHE_FILE = "data/manual/sox_history.json"


def _load_cache() -> list:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_cache(records: list):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def _fetch_history(ticker: str, period: str = "5y", interval: str = "1mo",
                   max_retries: int = 1) -> list:
    """拉月度收盘价（只试一次避免 yfinance 限流时长时间等待）"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period, interval=interval)
        if hist is not None and not hist.empty:
            data = []
            for idx, row in hist.iterrows():
                close = float(row["Close"])
                if close <= 0:
                    continue
                data.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "close": close,
                })
            return data
    except Exception as e:
        logger.warning(f"yfinance {ticker} failed: {e}")
    return []


def fetch_semiconductor_proxy() -> dict:
    """
    拉半导体指数月度数据
    计算月环比 + 3 月环比

    流程:
    1. 尝试 yfinance
    2. 失败则用本地缓存 (data/manual/sox_history.json)
    """
    # 先尝试 cache（避免被 yfinance 限流卡住）
    cache = _load_cache()
    history = None
    used_ticker = None

    if cache and len(cache) >= 3:
        # 优先用 cache，但如果有更新的 yfinance 数据再覆盖
        # 简单起见：直接用 cache
        history = cache
        used_ticker = "local_cache"
        logger.info(f"Using local SOX cache: {len(cache)} points")
    else:
        # 没 cache 才尝试 yfinance
        for ticker in TICKER_CANDIDATES:
            h = _fetch_history(ticker, period="5y")
            if h and len(h) >= 3:
                history = h
                used_ticker = ticker
                _save_cache(h)  # 缓存
                break

    if not history or len(history) < 3:
        return {
            "error": "no semiconductor data from any ticker or cache",
            "candidates_tried": TICKER_CANDIDATES,
            "hint": "用 seed_sox_history.py 注入历史数据，或 manual_input.py 录入",
        }

    # 缓存到本地（如果是从 yfinance 拉的）
    if used_ticker != "local_cache":
        _save_cache(history)

    # 写原始价格到 DB
    for h in history:
        db.insert_data(
            "semiconductor_index", h["close"], obs_date=h["date"],
            source=f"yfinance_{used_ticker}",
            raw_payload={"ticker": used_ticker},
        )

    # 计算月环比
    if len(history) >= 2:
        latest = history[-1]
        prev = history[-2]
        mom_pct = (latest["close"] - prev["close"]) / prev["close"] * 100
    else:
        mom_pct = 0

    # 计算 3 月环比（保留作参考）
    if len(history) >= 4:
        three_ago = history[-4]
        m3_pct = (latest["close"] - three_ago["close"]) / three_ago["close"] * 100
    else:
        m3_pct = 0

    # 写指标到 DB — 月环比
    obs_date = latest["date"]
    db.insert_data(
        "semiconductor_proxy", mom_pct, obs_date=obs_date,
        source=f"yfinance_{used_ticker}",
        raw_payload={"mom_pct": mom_pct, "m3_pct": m3_pct, "close": latest["close"]},
    )
    return {
        "metric_key": "semiconductor_proxy",
        "value": mom_pct,
        "obs_date": obs_date,
        "mom_pct": mom_pct,
        "m3_pct": m3_pct,
        "close": latest["close"],
        "ticker": used_ticker,
    }


def add_manual_record(date_str: str, close: float):
    """手动添加 SOX 月度收盘价（用于 yfinance 限流时）"""
    cache = _load_cache()
    cache = [r for r in cache if r["date"] != date_str]
    cache.append({"date": date_str, "close": float(close)})
    cache.sort(key=lambda x: x["date"])
    _save_cache(cache)
    logger.info(f"Added manual SOX record: {date_str} = {close}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_semiconductor_proxy())
