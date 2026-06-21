"""
AI 泡沫指数 0-100 计算

每个指标值 → 标准化为 0-100（100 = 最泡沫）→ 加权汇总

设计原则:
- 0-20:  极度低估 / 恐慌（机会建仓）
- 20-40: 中性
- 40-60: 警觉（分批减仓）
- 60-80: 泡沫（大幅减仓）
- 80-100: 极度泡沫（清仓或做空）
"""
import logging
from datetime import date, datetime
from typing import Optional

import db
from config import INDEX_WEIGHTS, INDEX_BANDS

logger = logging.getLogger(__name__)


def _score_metric(metric_key: str, value: Optional[float]) -> Optional[float]:
    """把单个指标值标准化为 0-100"""
    if value is None or metric_key not in INDEX_WEIGHTS:
        return None
    weight, healthy, peak, direction = INDEX_WEIGHTS[metric_key]
    if peak == healthy:
        return None
    if direction == "lower_is_better":
        # 值越低越健康
        if value <= healthy:
            return 0.0
        if value >= peak:
            return 100.0
        return 100.0 * (value - healthy) / (peak - healthy)
    else:
        # 值越高越健康
        if value >= healthy:
            return 0.0
        if value <= peak:
            return 100.0
        return 100.0 * (peak - value) / (peak - healthy)


def _score_semiconductor(value: float) -> float:
    """
    半导体指数得分（与通用 _score_metric 不同，因为语义是"涨太多=泡沫"）
    - 0% 涨 = 0 分
    - 25% 涨 = 100 分
    - 涨越多越泡沫
    """
    if value <= 0:
        return 0.0
    if value >= 25:
        return 100.0
    return 100.0 * value / 25


def _band(score: float) -> tuple:
    """根据分值返回 (label, color, advice)"""
    for key, (low, high, label, color, advice) in INDEX_BANDS.items():
        if low <= score < high:
            return label, color, advice
    return "未知", "#6b7280", "—"


def compute_index() -> dict:
    """
    计算当前 0-100 泡沫指数

    Returns:
        {
            "score": 67.5,
            "band": ("🟠 泡沫", "#ea580c", "大幅减仓"),
            "metrics_with_score": [
                {"key": "shiller_cape", "value": 41.71, "score": 80.5, "weight": 0.15, ...},
                ...
            ],
            "missing": ["gpu_spot_decline"],
        }
    """
    metrics_with_score = []
    missing = []
    score_sum = 0.0
    weight_sum = 0.0

    for metric_key, (weight, healthy, peak, direction) in INDEX_WEIGHTS.items():
        latest = db.get_latest(metric_key)
        if not latest or latest.get("value") is None:
            missing.append(metric_key)
            continue
        value = latest["value"]
        score = _score_metric(metric_key, value)
        if score is None:
            missing.append(metric_key)
            continue
        metrics_with_score.append({
            "key": metric_key,
            "value": value,
            "score": score,
            "weight": weight,
            "healthy": healthy,
            "peak": peak,
            "direction": direction,
            "obs_date": latest["obs_date"],
        })
        score_sum += score * weight
        weight_sum += weight

    # 如果有指标缺失，按现有指标的权重之和归一化
    if weight_sum > 0:
        final_score = score_sum / weight_sum
    else:
        final_score = None

    band = _band(final_score) if final_score is not None else ("无数据", "#6b7280", "—")

    return {
        "score": final_score,
        "band_label": band[0],
        "band_color": band[1],
        "band_advice": band[2],
        "metrics_with_score": metrics_with_score,
        "missing": missing,
        "weight_coverage": weight_sum,
    }


def save_index_to_db(score: float, band_label: str, obs_date: str = None) -> int:
    """把指数保存到 DB（用于画历史曲线）"""
    if obs_date is None:
        obs_date = date.today().isoformat()
    db.insert_data(
        "ai_bubble_index", score, obs_date=obs_date,
        source="computed",
        raw_payload={"band": band_label},
    )
    return 1


def get_index_history(limit: int = 240) -> list:
    """取指数历史"""
    return db.get_history("ai_bubble_index", limit=limit)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = compute_index()
    print(f"指数分: {result['score']:.1f}")
    print(f"档位: {result['band_label']} - {result['band_advice']}")
    print(f"指标覆盖: {result['weight_coverage']*100:.0f}%")
    print(f"缺失: {result['missing']}")
    print()
    for m in result["metrics_with_score"]:
        print(f"  {m['key']:25s} = {m['value']:8.2f}  → score={m['score']:5.1f}  "
              f"weight={m['weight']*100:4.1f}%")
