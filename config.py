"""
AI 泡沫监控仪表盘 - 阈值与配置

所有阈值来自前一轮研究：
- 高盛 Oppenheimer 报告
- 拾象 AI Bubble 讨论
- Grantham GMO 泡沫指标
- Burry / Kaplan / Spitznagel 等多方观点
- dot-com 2000 峰值作为历史参照
"""

# ============================================================================
# 指标阈值（alert / danger 两档）
# ============================================================================

# 指标 1: AI 数据中心 Capex/收入比
# 注：Mag 4 (MSFT/AMZN/GOOGL/META) 季度 capex / 估算的 AI 业务收入
# 行业级口径（全行业 capex/AI 总收入）历史峰值约 6x，
# 公司级口径（Mag 4 capex / Mag 4 AI 业务收入）阈值按实际数据校准
CAPEX_REVENUE = {
    "name_zh": "Mag 4 Capex/AI 收入比",
    "name_en": "Mag 4 Capex / AI Revenue",
    "unit": "x",
    "alert": 2.5,        # >2.5 进入警觉
    "danger": 4.0,       # >4.0 进入撤退
    "dotcom_peak": 6.0,  # 行业级口径
    "source": "SEC EDGAR XBRL",
    "direction": "lower_is_better",  # 越低越健康
    "update_freq": "quarterly",
}

# 指标 2: Shiller CAPE + Mag 7 集中度（双指标）
SHILLER_CAPE = {
    "name_zh": "Shiller 席勒市盈率 (CAPE)",
    "name_en": "Shiller CAPE",
    "unit": "x",
    "alert": 35.0,
    "danger": 40.0,
    "dotcom_peak": 44.0,
    "source": "Grantham / Krugman",
    "direction": "lower_is_better",
    "update_freq": "daily",
}

MAG7_CONCENTRATION = {
    "name_zh": "Mag 7 占 S&P 500 权重",
    "name_en": "Mag 7 / S&P 500 Weight",
    "unit": "%",
    "alert": 30.0,
    "danger": 33.0,
    "dotcom_peak": 18.0,  # dot-com 时 7 大约 18-20%
    "source": "GS Oppenheimer / 新华财经",
    "direction": "lower_is_better",
    "update_freq": "daily",
}

# 指标 3: SOX 半导体指数 月环比
SEMICONDUCTOR_PROXY = {
    "name_zh": "SOX 半导体指数 月环比",
    "name_en": "PHLX Semiconductor MoM",
    "unit": "%",
    "alert": 5.0,        # 月环比 >+5% 警觉
    "danger": 10.0,      # >+10% 危险
    "dotcom_peak": 15.0,  # 历史极端
    "source": "yfinance ^SOX",
    "direction": "lower_is_better",  # 涨=泡沫, 跌或平=健康
    "update_freq": "monthly",
}

# 旧 GPU 价格指标保留但已弃用
GPU_SPOT_DECLINE = SEMICONDUCTOR_PROXY  # 兼容旧代码

# 指标 4: 内部人卖出 + 散户保证金（双指标）
INSIDER_SELL_RATIO = {
    "name_zh": "Mag 7 内部人卖/买比 (6 个月)",
    "name_en": "Mag 7 Insider Sell/Buy Ratio (6M)",
    "unit": "x",
    "alert": 3.0,
    "danger": 5.0,
    "dotcom_peak": 4.0,
    "source": "openinsider.com / Form 4",
    "direction": "lower_is_better",
    "update_freq": "monthly",
}

RETAIL_MARGIN_GROWTH = {
    "name_zh": "FINRA Margin Debt (已弃用)",
    "name_en": "DEPRECATED",
    "unit": "%",
    "alert": 30.0,
    "danger": 45.0,
    "dotcom_peak": 50.0,
    "source": "DEPRECATED",
    "direction": "lower_is_better",
    "update_freq": "DEPRECATED",
}

# 指标 5: Capex 增速 vs 收入增速
CAPEX_VS_REVENUE_GROWTH = {
    "name_zh": "Capex 增速 / 收入增速 (已弃用)",
    "name_en": "DEPRECATED",
    "unit": "x",
    "alert": 2.0,
    "danger": 3.0,
    "dotcom_peak": 2.5,
    "source": "DEPRECATED",
    "direction": "lower_is_better",
    "update_freq": "DEPRECATED",
}

# ============================================================================
# 红灯规则
# ============================================================================
RED_LIGHT_RULES = {
    "reduce_position": 3,   # 任意 3 个指标亮红灯 → 减仓
    "exit_position": 5,     # 任意 5 个指标亮红灯 → 清仓
}

# ============================================================================
# Mag 7 股票清单（用于 yfinance 拉市值）
# ============================================================================
MAG7_TICKERS = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet (Google)",
    "AMZN": "Amazon",
    "META": "Meta",
    "NVDA": "Nvidia",
    "TSLA": "Tesla",
}

# ============================================================================
# 泡沫指数 0-100 评分模型
# ============================================================================
# 每个指标需要定义：
#   - healthy: 健康水平（指数分=0）
#   - peak:    历史极值（指数分=100，通常用 dot-com 2000 峰值）
#   - weight:  权重（所有指标权重之和 = 1.0）
#
# 评分公式（lower_is_better）：
#   score = 100 * (value - healthy) / (peak - healthy)
#   截断到 [0, 100]
#
# 评分公式（higher_is_better）：
#   score = 100 * (peak - value) / (peak - healthy)
#   截断到 [0, 100]
# ============================================================================

INDEX_WEIGHTS = {
    # 指标 key : (weight, healthy, peak, direction)
    "capex_revenue":               (0.25, 1.5,  4.0,   "lower_is_better"),  # Mag 4 公司级
    "shiller_cape":                (0.20, 16.0, 44.0,  "lower_is_better"),  # 长期均值 16，2000 峰值 44
    "mag7_concentration":          (0.15, 15.0, 50.0,  "lower_is_better"),  # 1989 日本峰值 50%
    "semiconductor_proxy":         (0.20, -5.0, 10.0,  "lower_is_better"),  # 月环比：>5%警觉，>10%危险
    "insider_sell_ratio":          (0.20, 1.0,  5.0,   "lower_is_better"),  # 1.0 平，5.0 极端
}
# 权重总和应等于 1.0
assert abs(sum(w[0] for w in INDEX_WEIGHTS.values()) - 1.0) < 0.01, \
    f"INDEX_WEIGHTS 权重总和 = {sum(w[0] for w in INDEX_WEIGHTS.values())}, 应为 1.0"

# 指数分档（基于历史回测和前人研究）
INDEX_BANDS = {
    "extreme_greed":   (80, 100, "🔴 极度泡沫",   "#dc2626", "清仓或做空"),
    "greed":           (60, 80,  "🟠 泡沫",       "#ea580c", "大幅减仓"),
    "caution":         (40, 60,  "🟡 警觉",       "#f59e0b", "分批减仓"),
    "neutral":         (20, 40,  "🔵 中性",       "#3b82f6", "正常持仓"),
    "fear":            (0,  20,  "🟢 恐慌/低估", "#10b981", "机会建仓"),
}


# ============================================================================
# SEC EDGAR — Mag 7 CIK 映射
# ============================================================================
SEC_CIKS = {
    "MSFT":  "0000789019",
    "AMZN":  "0001018724",
    "GOOGL": "0001652044",
    "META":  "0001326801",   # 修正：之前写错了
    "NVDA":  "0001045810",
    "AAPL":  "0000320193",
    "TSLA":  "0001318605",
}

# Capex 字段（XBRL us-gaap 标签）
SEC_CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
]

# ============================================================================
# FRED API — Margin Debt Series
# ============================================================================
# FINRA Margin Debt 数据由 FINRA 提交给 FRED:
#   - BOGZ1FL073060003Q:  Margin Debt, Level (Quarterly, $ Billions, NSA)
#   - BAMLH0A0CM:         ICE BofA US High Yield OAS (可作对照)
# FRED 公开 CSV endpoint:
#   https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}
# ============================================================================
FRED_MARGIN_DEBT_SERIES = "BOGZ1FL073060003Q"

# ============================================================================
# RunPod / Paperspace GPU 目标机型
# ============================================================================
GPU_TARGETS = {
    "h100": "H100",        # 高端训练
    "a100": "A100",        # 中端
    "h200": "H200",        # 新一代
}

# ============================================================================
# 数据库与路径
# ============================================================================
DB_PATH = "data/ai_bubble.db"
LOG_DIR = "logs"
LOG_FILE = "logs/dashboard.log"
HTML_OUTPUT = "data/dashboard.html"  # 仪表盘最终输出（双击可用浏览器打开）

# 仪表盘标题与配色
DASHBOARD_TITLE = "AI 泡沫监控仪表盘"
DASHBOARD_SUBTITLE = "5 个 DIY 指标 · 实时盯盘 · 撤退信号自动提示"

# 配色
COLORS = {
    "green": "#10b981",   # 健康
    "yellow": "#f59e0b",  # 警觉
    "red": "#ef4444",     # 危险
    "blue": "#3b82f6",
    "gray": "#6b7280",
    "bg_dark": "#0f172a",
    "bg_card": "#1e293b",
    "text_primary": "#f1f5f9",
    "text_secondary": "#94a3b8",
    "border": "#334155",
}
