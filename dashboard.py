"""
仪表盘评估与生成模块
- evaluate(): 拉所有指标最新值，判定状态
- render_dashboard(): 生成 HTML 仪表盘（含指数大圆盘 + 历史曲线 + 回测报告）
"""
import os
import json
import logging
from datetime import date, datetime
from typing import Optional

import db
from config import (
    DASHBOARD_TITLE, DASHBOARD_SUBTITLE, COLORS, RED_LIGHT_RULES,
    CAPEX_REVENUE, SHILLER_CAPE, MAG7_CONCENTRATION, GPU_SPOT_DECLINE,
    INSIDER_SELL_RATIO, RETAIL_MARGIN_GROWTH, CAPEX_VS_REVENUE_GROWTH,
    INDEX_WEIGHTS, INDEX_BANDS,
)
from index import compute_index, save_index_to_db

logger = logging.getLogger(__name__)

METRICS_META = [
    {
        "metric_key": "capex_revenue",
        "name_zh": "Mag 4 Capex / AI 收入比",
        "name_en": "Mag 4 Capex / AI Revenue",
        "unit": "x",
        "alert_value": CAPEX_REVENUE["alert"],
        "danger_value": CAPEX_REVENUE["danger"],
        "dotcom_peak": CAPEX_REVENUE["dotcom_peak"],
        "source": "SEC EDGAR",
        "direction": "lower_is_better",
        "update_freq": "quarterly",
        "category": "fundamental",
        "desc": "MSFT/AMZN/GOOGL/META 四家 hyperscaler 季度资本开支合计除以估算的 AI 业务收入。AI 业务收入按 Mag 4 总收入 13% 估算 (Menlo 2025 / 拾象报告口径)，不直接等于公司细分披露的 AI 业务实际收入。",
    },
    {
        "metric_key": "shiller_cape",
        "name_zh": "Shiller CAPE (10 年平滑市盈率)",
        "name_en": "Shiller CAPE",
        "unit": "x",
        "alert_value": SHILLER_CAPE["alert"],
        "danger_value": SHILLER_CAPE["danger"],
        "dotcom_peak": SHILLER_CAPE["dotcom_peak"],
        "source": "Yale Shiller 公开数据",
        "direction": "lower_is_better",
        "update_freq": "daily",
        "category": "market",
        "desc": "S&P 500 指数除以 10 年平滑通胀调整后的实际 EPS。Robert Shiller 1988 年首次提出。>25x 长期均值，>35x 历史前 10%，>40x 仅 2000 年前后出现过。",
    },
    {
        "metric_key": "mag7_concentration",
        "name_zh": "Mag 7 占 S&P 500 权重",
        "name_en": "Mag 7 / S&P 500 Weight",
        "unit": "%",
        "alert_value": MAG7_CONCENTRATION["alert"],
        "danger_value": MAG7_CONCENTRATION["danger"],
        "dotcom_peak": MAG7_CONCENTRATION["dotcom_peak"],
        "source": "yfinance 实时市值",
        "direction": "lower_is_better",
        "update_freq": "daily",
        "category": "market",
        "desc": "AAPL/MSFT/GOOGL/AMZN/META/NVDA/TSLA 总市值除以 S&P 500 总市值。当前 32% 处于历史前 1% 区间。",
    },
    {
        "metric_key": "semiconductor_proxy",
        "name_zh": "SOX 半导体指数 月环比",
        "name_en": "PHLX Semiconductor MoM",
        "unit": "%",
        "alert_value": 5.0,
        "danger_value": 10.0,
        "dotcom_peak": 15.0,
        "source": "yfinance ^SOX (本地缓存: data/manual/sox_history.json)",
        "direction": "lower_is_better",
        "update_freq": "monthly",
        "category": "fundamental",
        "desc": "费城半导体指数 (^SOX) 月末收盘价的月度变化率。SOX 包含 NVDA/AMD/AVGO/ASML/MU/QCOM 等半导体龙头。分档: -5%~+5% 中性 (健康)，+5%~+10% 投机升温 (警觉)，>+10% 投机过热 (危险)。",
    },
    {
        "metric_key": "insider_sell_ratio",
        "name_zh": "Mag 7 内部人 6M 卖/买比",
        "name_en": "Mag 7 Insider Sell/Buy Ratio (6M)",
        "unit": "x",
        "alert_value": INSIDER_SELL_RATIO["alert"],
        "danger_value": INSIDER_SELL_RATIO["danger"],
        "dotcom_peak": INSIDER_SELL_RATIO["dotcom_peak"],
        "source": "OpenInsider / SEC Form 4",
        "direction": "lower_is_better",
        "update_freq": "monthly",
        "category": "sentiment",
        "desc": "Mag 7 全部 6 个月滚动窗口内部人卖出金额除以买入金额。比值 1.0 持平，>3.0 内部人净卖出显著，>5.0 接近历史极端水平 (2007/2021 顶峰)。",
    },
]


def _judge_status(metric: dict, value: Optional[float]) -> str:
    if value is None:
        return "gray"
    direction = metric["direction"]
    alert = metric["alert_value"]
    danger = metric["danger_value"]
    if direction == "lower_is_better":
        if value >= danger:
            return "red"
        if value >= alert:
            return "yellow"
        return "green"
    else:
        if value <= danger:
            return "red"
        if value <= alert:
            return "yellow"
        return "green"


def evaluate() -> dict:
    """评估所有指标最新状态 + 计算 0-100 指数"""
    metrics_status = []
    total_red = 0
    total_yellow = 0
    total_green = 0
    total_gray = 0

    for meta in METRICS_META:
        latest = db.get_latest(meta["metric_key"])
        value = latest["value"] if latest else None
        status = _judge_status(meta, value)
        if status == "red":
            total_red += 1
        elif status == "yellow":
            total_yellow += 1
        elif status == "green":
            total_green += 1
        else:
            total_gray += 1

        metrics_status.append({
            **meta,
            "value": value,
            "obs_date": latest["obs_date"] if latest else None,
            "obs_period": latest.get("obs_period") if latest else None,
            "status": status,
            "source_url": latest.get("source") if latest else None,
        })

    # 计算 0-100 指数
    index_data = compute_index()
    score = index_data.get("score")
    if score is not None:
        save_index_to_db(score, index_data["band_label"])

    # 总体建议（指数分优先 + 多指标）
    if score is not None:
        rec = index_data["band_advice"]
        rec_color = index_data["band_color"]
    else:
        rec = "无数据"
        rec_color = "#6b7280"

    eval_date = date.today().isoformat()
    result = {
        "eval_date": eval_date,
        "total_red": total_red,
        "total_yellow": total_yellow,
        "total_green": total_green,
        "total_gray": total_gray,
        "recommendation": rec,
        "rec_color": rec_color,
        "index": index_data,
        "metrics": metrics_status,
    }
    db.log_signal(eval_date, total_red, total_yellow, total_green, rec, {
        "index_score": score, "metrics": [
            {"key": m["metric_key"], "value": m["value"], "status": m["status"]}
            for m in metrics_status
        ]
    })
    return result


def render_dashboard(eval_data: dict, output_path: str = "data/dashboard.html"):
    """生成 HTML 仪表盘"""
    template_path = os.path.join("templates", "dashboard.html")
    if not os.path.exists(template_path):
        logger.error(f"Template not found: {template_path}")
        return

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    # 准备每张图的历史数据
    chart_data = []
    for m in eval_data["metrics"]:
        history = db.get_history(m["metric_key"], limit=180)
        history.reverse()
        chart_data.append({
            "key": m["metric_key"],
            "name_zh": m["name_zh"],
            "history": [{"date": h["obs_date"], "value": h["value"]} for h in history],
            "alert": m["alert_value"],
            "danger": m["danger_value"],
            "dotcom_peak": m.get("dotcom_peak"),
            "current_value": m["value"],
            "unit": m["unit"],
            "direction": m["direction"],
        })

    # 指数历史
    index_history = db.get_history("ai_bubble_index", limit=240)
    index_history.reverse()
    index_chart = [{"date": h["obs_date"], "value": h["value"]} for h in index_history]

    # 指数评分详情
    index_score_details = []
    for m in eval_data["index"].get("metrics_with_score", []):
        index_score_details.append({
            "key": m["key"],
            "value": m["value"],
            "score": round(m["score"], 1),
            "weight": m["weight"],
            "weighted_score": round(m["score"] * m["weight"], 1),
        })

    html = template
    html = html.replace("{{ title }}", DASHBOARD_TITLE)
    html = html.replace("{{ subtitle }}", DASHBOARD_SUBTITLE)
    html = html.replace("{{ eval_date }}", eval_data["eval_date"])
    html = html.replace("{{ total_red }}", str(eval_data["total_red"]))
    html = html.replace("{{ total_yellow }}", str(eval_data["total_yellow"]))
    html = html.replace("{{ total_green }}", str(eval_data["total_green"]))
    html = html.replace("{{ total_gray }}", str(eval_data["total_gray"]))
    html = html.replace("{{ recommendation }}", eval_data["recommendation"])
    html = html.replace("{{ rec_color }}", eval_data["rec_color"])

    # 指数相关
    idx = eval_data["index"]
    score_str = f"{idx['score']:.1f}" if idx.get("score") is not None else "—"
    html = html.replace("{{ index_score }}", score_str)
    html = html.replace("{{ index_band_label }}", idx.get("band_label", "—"))
    html = html.replace("{{ index_band_color }}", idx.get("band_color", "#6b7280"))
    html = html.replace("{{ index_band_advice }}", idx.get("band_advice", "—"))
    html = html.replace("{{ index_weight_coverage }}", f"{idx.get('weight_coverage', 0) * 100:.0f}%")
    html = html.replace("{{ index_score_details_json }}", json.dumps(index_score_details, ensure_ascii=False))
    html = html.replace("{{ index_history_json }}", json.dumps(index_chart, ensure_ascii=False))

    html = html.replace("{{ metrics_json }}", json.dumps(eval_data["metrics"], ensure_ascii=False))
    html = html.replace("{{ chart_data_json }}", json.dumps(chart_data, ensure_ascii=False))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"Dashboard rendered: {output_path}")


def main():
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/dashboard.html")
    args = parser.parse_args()

    db.init_db()
    db.upsert_meta(METRICS_META)
    eval_data = evaluate()
    render_dashboard(eval_data, args.output)
    print(f"✓ Dashboard generated: {args.output}")
    idx = eval_data["index"]
    if idx.get("score") is not None:
        print(f"  指数分: {idx['score']:.1f} ({idx['band_label']})")
        print(f"  建议: {eval_data['recommendation']}")
        print(f"  指标覆盖: {idx['weight_coverage']*100:.0f}%, 缺失: {idx['missing']}")


if __name__ == "__main__":
    main()
