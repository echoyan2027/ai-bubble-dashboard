"""
指标 ③: 半导体指数月环比 — 用 SOXX / SMH ETF (替代 ^SOX)

数据源:
- 主要: 腾讯财经 K线 API (国内可访问, 无需 key, 不被墙)
  https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=usSOXX,month,,,320,qfq
- 备选: 本地缓存 data/manual/sox_history.json

为什么换 ^SOX 为 SOXX:
- ^SOX 是 PHLX 半导体指数 (无 ETF 形态直接投资), 腾讯/东财都不直接覆盖
- SOXX 是 iShares Semiconductor ETF, 跟踪 ICE Semiconductor 25/35 Index, 持仓与 ^SOX 高度重合 (NVDA/AMD/AVGO/ASML 等)
- SMH 是 VanEck Semiconductor ETF, 跟踪 MVIS US Listed Semiconductor 25 Index, 也可作为 fallback
"""
import os
import json
import logging
import time
from datetime import date, datetime
import requests

import db

logger = logging.getLogger(__name__)

# 候选 ticker (腾讯 K线 API 格式: us{symbol})
TICKER_CANDIDATES = ["usSOXX", "usSMH"]
CACHE_FILE = "data/manual/sox_history.json"
# 腾讯 K线 API (month K线, 320 根 ≈ 26年, 够用)
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={ticker},month,,,320,qfq"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _make_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": UA,
        "Referer": "https://gu.qq.com/",
        "Accept": "application/json",
    })
    return sess


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


def _fetch_history(ticker: str) -> list:
    """从腾讯 K线 API 拉月度收盘价
    返回: [{"date": "2025-01-31", "close": 234.56}, ...]
    注: 腾讯 K线 API 实际只返回当前数据, 不给历史. 此函数保留作为 fallback.
    """
    sess = _make_session()
    try:
        resp = sess.get(KLINE_URL.format(ticker=ticker), timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Tencent K-line {ticker} failed: {e}")
        return []

    payload = data.get("data", {}).get(ticker, {})
    klines = payload.get("month") or payload.get("qfqmonth") or []
    if not klines:
        logger.warning(f"Tencent K-line {ticker} returned no month data")
        return []

    out = []
    for row in klines:
        # 格式: ["2025-01-31", close, open, high, low, volume, ...]
        if len(row) < 5:
            continue
        try:
            d = row[0]
            close = float(row[1])
            if close <= 0:
                continue
            out.append({"date": d, "close": close})
        except Exception:
            continue
    return out


def _tencent_realtime_price(symbol: str) -> float | None:
    """从腾讯实时 API 拿当前价格, 用于补充 cache 缺失的最新点
    symbol 例: 'usSOXX' (不带 us 前缀也可)
    """
    if not symbol.startswith("us"):
        symbol = "us" + symbol
    sess = _make_session()
    url = f"https://qt.gtimg.cn/q={symbol}"
    try:
        resp = sess.get(url, timeout=10)
        resp.raise_for_status()
        for line in resp.text.strip().split("\n"):
            if "=" not in line:
                continue
            _, val = line.split("=", 1)
            parts = val.strip().strip('";\n ').split("~")
            if len(parts) > 3:
                return float(parts[3])
    except Exception as e:
        logger.warning(f"Tencent realtime {symbol} failed: {e}")
    return None


def fetch_semiconductor_proxy() -> dict:
    """
    拉半导体 ETF 月度数据 (SOXX/SMH, 替代 ^SOX)
    计算月环比

    流程:
    1. 尝试腾讯 K线 API
    2. 失败则用本地 cache
    """
    cache = _load_cache()

    history = None
    used_ticker = None
    last_error = None

    # 第一步: 尝试腾讯 K线
    for ticker in TICKER_CANDIDATES:
        h = _fetch_history(ticker)
        if h and len(h) >= 3:
            # 如果 cache 有更早数据, 合并
            if cache:
                h_dates = {x["date"] for x in h}
                older = [c for c in cache if c["date"] < min(h_dates)]
                h = older + h
            history = h
            used_ticker = ticker
            _save_cache(h)
            logger.info(f"Got {len(h)} month-K points from Tencent {ticker}")
            break
        last_error = f"{ticker}: no data"

    # 第二步: 失败用 cache
    if not history and cache and len(cache) >= 3:
        history = cache
        used_ticker = "local_cache"
        logger.warning(f"Tencent API failed ({last_error}), using local cache: {len(cache)} points")

    if not history or len(history) < 3:
        return {
            "error": "no semiconductor data from any ticker or cache",
            "candidates_tried": TICKER_CANDIDATES,
            "tencent_error": last_error,
            "hint": "用 seed_sox_history.py 注入历史数据, 或 manual_input.py 录入",
        }

    # 写原始价格到 DB
    for h in history:
        db.insert_data(
            "semiconductor_index", h["close"], obs_date=h["date"],
            source=f"tencent_{used_ticker}",
            raw_payload={"ticker": used_ticker},
        )

    # 关键补充: 用腾讯实时价格补 cache 缺失的"今天"这个点
    # 不管是 cache 模式还是腾讯 K线模式, 都用实时价格补最新点
    cache_latest_date = history[-1]["date"]
    today_str = date.today().isoformat()
    if today_str > cache_latest_date:
        # 选择一个 ticker 来拿实时价
        rt_ticker = TICKER_CANDIDATES[0]  # usSOXX 优先
        live_price = _tencent_realtime_price(rt_ticker)
        if live_price and live_price > 0:
            history.append({"date": today_str, "close": live_price})
            _save_cache(history)
            logger.info(f"Appended today's live price: {rt_ticker} = {live_price} on {today_str}")
            db.insert_data(
                "semiconductor_index", live_price, obs_date=today_str,
                source=f"tencent_realtime_{rt_ticker}",
                raw_payload={"ticker": rt_ticker, "type": "live"},
            )

    # 计算月环比 (关键修复: 用最近月末点作为基准, 不用任意 mid-month 点)
    # cache 里可能有 mid-month 点 (e.g. 2026-06-19), 但月环比应该比"上一月末 vs 当前"
    def _last_month_end(history_list, from_idx=0):
        """从 history[from_idx] 往前找最近的月末 (day >= 25)"""
        for i in range(len(history_list) - 1, from_idx - 1, -1):
            d = history_list[i]["date"]
            try:
                day = int(d.split("-")[2])
                if day >= 25:
                    return history_list[i]
            except Exception:
                continue
        return None

    if len(history) >= 2:
        latest = history[-1]
        # 找上一个月末 (skip mid-month points)
        prev = _last_month_end(history, from_idx=0)
        # 如果没找到, 用 history[-2] 兜底
        if prev is None or prev["date"] == latest["date"]:
            prev = history[-2]
        mom_pct = (latest["close"] - prev["close"]) / prev["close"] * 100
    else:
        mom_pct = 0

    # 3M 环比 (保留)
    if len(history) >= 4:
        # 3M 环比: 找 3 个月前的月末
        latest_month_end = _last_month_end(history)
        if latest_month_end is None:
            latest_month_end = history[-1]
        # 找 index where date is approximately 3 months earlier
        try:
            latest_dt = datetime.strptime(latest_month_end["date"], "%Y-%m-%d")
            target_dt = latest_dt.replace(month=latest_dt.month - 3) if latest_dt.month > 3 else latest_dt.replace(year=latest_dt.year - 1, month=latest_dt.month + 9)
            target_str = target_dt.strftime("%Y-%m")
            three_ago = None
            for h in history:
                if h["date"].startswith(target_str):
                    three_ago = h
                    break
            if three_ago:
                m3_pct = (latest_month_end["close"] - three_ago["close"]) / three_ago["close"] * 100
            else:
                m3_pct = 0
        except Exception:
            m3_pct = 0
    else:
        m3_pct = 0

    obs_date = latest["date"]
    db.insert_data(
        "semiconductor_proxy", mom_pct, obs_date=obs_date,
        source=f"tencent_{used_ticker}",
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
    """手动添加 SOX 月度收盘价 (用于腾讯 API 异常时)"""
    cache = _load_cache()
    cache = [r for r in cache if r["date"] != date_str]
    cache.append({"date": date_str, "close": float(close)})
    cache.sort(key=lambda x: x["date"])
    _save_cache(cache)
    logger.info(f"Added manual SOX record: {date_str} = {close}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_semiconductor_proxy())
