"""fetchers package — 核心 5 个指标 + 2 个补充指标"""
from .shiller_mag7 import fetch_shiller, fetch_mag7_concentration
from .capex_revenue import fetch_capex_revenue
from .aws_spot_gpu import fetch_semiconductor_proxy
from .insider import fetch_insider_sell_ratio
from .memory_storage import fetch_storage_proxy
from .ai_upstream_profit import fetch_ai_upstream_profit
