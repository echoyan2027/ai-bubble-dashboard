"""fetchers package — 5 个数据采集器 (当前保留 5 个)"""
from .shiller_mag7 import fetch_shiller, fetch_mag7_concentration
from .capex_revenue import fetch_capex_revenue
from .aws_spot_gpu import fetch_semiconductor_proxy
from .insider import fetch_insider_sell_ratio
