"""
指标 ⑥: 存储紧缺度代理 (基于兆易创新 K 线)

为什么用 A 股存储代理:
- DDR5/NAND 现货价 (TrendForce/DRAMeXchange) 公开 API 不可用
- 兆易创新 (603986.SH) 是国内 NOR Flash 龙头 + 利基 DRAM
- 北京君正 (300223.SZ) 主营 DRAM 芯片
- 两者股价对存储周期高度敏感, 是公开可获取的最佳代理

数据源: 新浪财经 A 股 K 线 API (JSONP 格式, 国内可访问)
"""
import re
import json
import logging
from datetime import date, datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import db

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"
# 兆易创新 sh603986, 北京君正 sz300223, 紫光国微 sz002049
STORAGE_STOCKS = [
    {"code": "603986", "symbol": "sh603986", "name": "兆易创新"},
    {"code": "300223", "symbol": "sz300223", "name": "北京君正"},
    {"code": "002049", "symbol": "sz002049", "name": "紫光国微"},
]


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
    sess.headers.update({"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
    return sess


def _fetch_kline(symbol: str, scale: int = 240, datalen: int = 1023) -> list:
    """拉 A 股日 K 线 (scale=240=日 K)
    返回: [{"date": "2026-07-03", "open": ..., "close": ..., "high": ..., "low": ..., "volume": ...}, ...]
    """
    sess = _make_session()
    url = SINA_KLINE_URL
    params = {"symbol": symbol, "scale": scale, "ma": "no", "datalen": datalen}
    try:
        resp = sess.get(url, params=params, timeout=15)
        resp.raise_for_status()
        text = resp.text
        # 解析 JSONP: var=([...]);
        idx = text.find("var=")
        if idx < 0:
            logger.warning(f"Sina kline {symbol}: no 'var=' marker")
            return []
        raw = text[idx + 4:].strip()
        if raw.endswith(";"):
            raw = raw[:-1].strip()
        # Sina 实际格式是 var=([...]), 需要剥掉外层括号
        if raw.startswith("(") and raw.endswith(")"):
            raw = raw[1:-1].strip()
        try:
            data = json.loads(raw)
        except Exception as e:
            logger.warning(f"Sina kline {symbol}: JSON parse failed: {e}, raw[:200]={raw[:200]}")
            return []
        out = []
        for row in data:
            d = row.get("day", "")
            try:
                close = float(row.get("close", 0))
                open_p = float(row.get("open", 0))
                high = float(row.get("high", 0))
                low = float(row.get("low", 0))
                volume = float(row.get("volume", 0))
                if close <= 0 or volume <= 0:
                    continue
                out.append({
                    "date": d,
                    "open": open_p,
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                })
            except Exception:
                continue
        return out
    except Exception as e:
        logger.warning(f"Sina kline {symbol} failed: {e}")
        return []


def fetch_storage_proxy() -> dict:
    """
    拉兆易创新/北京君正 K 线, 计算月环比作为 DDR5/NAND 紧缺度代理
    输出: mom_pct (最新月环比), m3_pct (3 月环比), obs_date
    """
    all_history = {}
    for s in STORAGE_STOCKS:
        k = _fetch_kline(s["symbol"])
        if k:
            all_history[s["code"]] = k

    if not all_history:
        return {"error": "no storage stock data from Sina"}

    # 用兆易创新 (国内存储龙头) 作为主代理
    primary = all_history.get("603986")
    if not primary or len(primary) < 30:
        return {"error": "primary stock (兆易创新) has insufficient data",
                "available": list(all_history.keys())}

    # 写价格历史到 DB (保留 30 天, 减少存储)
    for code, history in all_history.items():
        for h in history[-30:]:
            db.insert_data(
                f"storage_{code}", h["close"], obs_date=h["date"],
                source="sina_kline",
                raw_payload={"open": h["open"], "high": h["high"],
                             "low": h["low"], "volume": h["volume"]},
            )

    # 计算月环比 (找最近月末 vs 上月末)
    def last_month_end(history):
        for h in reversed(history):
            try:
                if int(h["date"].split("-")[2]) >= 25:
                    return h
            except Exception:
                continue
        return None

    if len(primary) < 2:
        return {"error": "insufficient history for primary"}

    latest = primary[-1]
    prev = last_month_end(primary)
    if prev is None or prev["date"] == latest["date"]:
        prev = primary[-2]

    mom_pct = (latest["close"] - prev["close"]) / prev["close"] * 100

    # 3M 环比
    m3_pct = 0
    try:
        latest_dt = datetime.strptime(latest["date"], "%Y-%m-%d")
        target_month = latest_dt.month - 3
        target_year = latest_dt.year
        if target_month <= 0:
            target_month += 12
            target_year -= 1
        target_str = f"{target_year}-{target_month:02d}"
        three_ago = None
        for h in primary:
            if h["date"].startswith(target_str) and int(h["date"].split("-")[2]) >= 25:
                three_ago = h
                break
        if three_ago:
            m3_pct = (latest["close"] - three_ago["close"]) / three_ago["close"] * 100
    except Exception:
        pass

    obs_date = latest["date"]
    db.insert_data(
        "storage_proxy", mom_pct, obs_date=obs_date,
        source="sina_兆易创新",
        raw_payload={
            "mom_pct": mom_pct, "m3_pct": m3_pct, "close": latest["close"],
            "primary": "兆易创新(603986)", "secondary": list(all_history.keys()),
        },
    )
    return {
        "metric_key": "storage_proxy",
        "value": mom_pct,
        "obs_date": obs_date,
        "mom_pct": mom_pct,
        "m3_pct": m3_pct,
        "close": latest["close"],
        "primary_stock": "兆易创新",
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_storage_proxy())