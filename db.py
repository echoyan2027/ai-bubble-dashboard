"""
SQLite 数据库封装
- 三张表：metric_meta（指标元数据）、metric_data（历史数据）、signal_log（信号历史）
- 提供 upsert / query / latest 三个核心操作
"""
import sqlite3
import os
from datetime import datetime, date
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# 关键修复: 用 __file__ 锁定 DB 绝对路径, 不受 CWD 影响
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_THIS_DIR, "data", "ai_bubble.db")

SCHEMA = """
-- 指标元数据（阈值、单位、刷新频率等）
CREATE TABLE IF NOT EXISTS metric_meta (
    metric_key TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL,
    name_en TEXT NOT NULL,
    unit TEXT,
    alert_value REAL,
    danger_value REAL,
    dotcom_peak REAL,
    source TEXT,
    direction TEXT,           -- lower_is_better / higher_is_better
    update_freq TEXT,         -- daily / weekly / biweekly / monthly / quarterly
    category TEXT,            -- market / fundamental / sentiment
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 指标历史值
CREATE TABLE IF NOT EXISTS metric_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_key TEXT NOT NULL,
    obs_date TEXT NOT NULL,    -- YYYY-MM-DD
    obs_period TEXT,           -- e.g. '2025Q3' for quarterly
    value REAL NOT NULL,
    raw_payload TEXT,          -- JSON 原文（调试用）
    source TEXT,               -- 抓取来源标识
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(metric_key, obs_date, obs_period)
);

-- 红灯信号历史（每次评估的快照）
CREATE TABLE IF NOT EXISTS signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_date TEXT NOT NULL,    -- YYYY-MM-DD
    total_red INTEGER NOT NULL,
    total_yellow INTEGER NOT NULL,
    total_green INTEGER NOT NULL,
    recommendation TEXT,        -- '持仓' / '减仓' / '清仓'
    details TEXT,               -- JSON：每个指标的状态
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_metric_data_key_date
    ON metric_data(metric_key, obs_date DESC);

CREATE INDEX IF NOT EXISTS idx_signal_log_date
    ON signal_log(eval_date DESC);
"""


@contextmanager
def get_conn():
    """上下文管理：自动 commit / close"""
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """初始化表结构"""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    logger.info(f"DB initialized at {DB_PATH}")


def upsert_meta(metric_meta_list: list):
    """批量写入指标元数据"""
    with get_conn() as conn:
        for m in metric_meta_list:
            conn.execute(
                """
                INSERT OR REPLACE INTO metric_meta
                  (metric_key, name_zh, name_en, unit, alert_value, danger_value,
                   dotcom_peak, source, direction, update_freq, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m["metric_key"], m["name_zh"], m["name_en"], m["unit"],
                    m["alert_value"], m["danger_value"], m["dotcom_peak"],
                    m["source"], m["direction"], m["update_freq"], m.get("category", ""),
                ),
            )
    logger.info(f"Upserted {len(metric_meta_list)} metric meta records")


def insert_data(metric_key: str, value: float, obs_date: str = None,
                obs_period: str = None, raw_payload: dict = None,
                source: str = None):
    """插入一条指标数据（同 metric_key+date+period 会替换）"""
    if obs_date is None:
        obs_date = date.today().isoformat()
    import json
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO metric_data
              (metric_key, obs_date, obs_period, value, raw_payload, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                metric_key, obs_date, obs_period, value,
                json.dumps(raw_payload, ensure_ascii=False) if raw_payload else None,
                source,
            ),
        )
    logger.info(f"Inserted {metric_key} = {value} on {obs_date} (period={obs_period})")


def get_latest(metric_key: str) -> dict | None:
    """取某指标最新一条"""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM metric_data
            WHERE metric_key = ?
            ORDER BY obs_date DESC, id DESC LIMIT 1
            """,
            (metric_key,),
        ).fetchone()
        return dict(row) if row else None


def get_history(metric_key: str, limit: int = 180) -> list:
    """取某指标历史"""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM metric_data
            WHERE metric_key = ?
            ORDER BY obs_date DESC
            LIMIT ?
            """,
            (metric_key, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_meta() -> list:
    """取所有指标元数据"""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM metric_meta ORDER BY metric_key").fetchall()
        return [dict(r) for r in rows]


def log_signal(eval_date: str, total_red: int, total_yellow: int,
               total_green: int, recommendation: str, details: dict):
    """记录一次信号评估"""
    import json
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO signal_log
              (eval_date, total_red, total_yellow, total_green, recommendation, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (eval_date, total_red, total_yellow, total_green,
             recommendation, json.dumps(details, ensure_ascii=False)),
        )
    logger.info(f"Logged signal: {recommendation} ({total_red} red / {total_yellow} yellow)")


def get_latest_signal() -> dict | None:
    """取最近一次信号评估"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM signal_log ORDER BY eval_date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("DB ready.")
