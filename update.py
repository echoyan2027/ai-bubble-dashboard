"""
主入口 — 拉取数据 + 评估 + 渲染仪表盘

用法:
    python update.py                  # 跑全部自动抓取 + 渲染
    python update.py --auto-only      # 只跑能自动抓的（跳过手工录入的）
    python update.py --render-only    # 只重新渲染（用现有 DB 数据）
    python update.py --quiet          # 静默模式
"""
import sys
import os
import argparse
import logging
from datetime import date
import time

# 把项目根目录加到 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from config import LOG_DIR, LOG_FILE
import dashboard
from fetchers import (
    fetch_shiller, fetch_mag7_concentration,
    fetch_capex_revenue,
    fetch_semiconductor_proxy,
    fetch_insider_sell_ratio,
)


def setup_logging(verbose: bool = True):
    os.makedirs(LOG_DIR, exist_ok=True)
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def run_all(auto_only: bool = False):
    """拉取所有可自动抓的数据"""
    logger = logging.getLogger("update")
    results = {}

    # 1. Shiller CAPE（自动）
    logger.info("[1/5] 抓取 Shiller CAPE...")
    try:
        results["shiller_cape"] = fetch_shiller()
        logger.info(f"  → {results['shiller_cape']}")
    except Exception as e:
        logger.error(f"  ✗ {e}")
        results["shiller_cape"] = {"error": str(e)}

    # 2. Mag 7 集中度（自动）
    logger.info("[2/5] 抓取 Mag 7 集中度...")
    try:
        results["mag7_concentration"] = fetch_mag7_concentration()
        logger.info(f"  → {results['mag7_concentration']}")
    except Exception as e:
        logger.error(f"  ✗ {e}")
        results["mag7_concentration"] = {"error": str(e)}

    # 3. SOX 半导体指数（自动）
    logger.info("[3/5] 抓取 SOX 半导体指数...")
    try:
        results["semiconductor_proxy"] = fetch_semiconductor_proxy()
        logger.info(f"  → {results['semiconductor_proxy']}")
    except Exception as e:
        logger.error(f"  ✗ {e}")
        results["semiconductor_proxy"] = {"error": str(e)}

    # 4. 内部人卖出（自动）
    logger.info("[4/5] 抓取 Mag 7 内部人卖出比...")
    try:
        results["insider_sell_ratio"] = fetch_insider_sell_ratio()
        logger.info(f"  → {results['insider_sell_ratio']}")
    except Exception as e:
        logger.error(f"  ✗ {e}")
        results["insider_sell_ratio"] = {"error": str(e)}

    if auto_only:
        return results

    # 5. Capex/收入（依赖手工录入的季度数据）
    logger.info("[5/5] 计算 Capex/收入比（需手工录入）...")
    try:
        results["capex_revenue"] = fetch_capex_revenue()
        logger.info(f"  → {results['capex_revenue']}")
    except Exception as e:
        logger.error(f"  ✗ {e}")
        results["capex_revenue"] = {"error": str(e)}

    return results


def main():
    parser = argparse.ArgumentParser(description="AI 泡沫监控 - 主入口")
    parser.add_argument("--auto-only", action="store_true",
                        help="只跑自动抓取，跳过手工录入相关")
    parser.add_argument("--render-only", action="store_true",
                        help="只渲染仪表盘，不抓取")
    parser.add_argument("--output", default="data/dashboard.html",
                        help="仪表盘输出路径")
    parser.add_argument("--quiet", action="store_true", help="静默模式")
    args = parser.parse_args()

    # 关键修复: 不管用户从哪个目录运行 update.py, 都先把 CWD 切到脚本所在目录
    # 这样 data/dashboard.html 相对路径和 templates/dashboard.html 都能找到
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    setup_logging(verbose=not args.quiet)
    logger = logging.getLogger("update")

    # 1. 初始化 DB
    db.init_db()
    db.upsert_meta(dashboard.METRICS_META)

    # 2. 拉取数据
    if not args.render_only:
        logger.info("=" * 60)
        logger.info("开始拉取数据...")
        logger.info("=" * 60)
        start = time.time()
        run_all(auto_only=args.auto_only)
        logger.info(f"数据拉取完成，耗时 {time.time() - start:.1f}s")
    else:
        logger.info("跳过数据拉取（--render-only）")

    # 3. 评估
    logger.info("评估指标状态...")
    eval_data = dashboard.evaluate()
    logger.info(f"  红灯 {eval_data['total_red']} / 黄灯 {eval_data['total_yellow']} / "
                f"绿灯 {eval_data['total_green']} / 灰 {eval_data['total_gray']}")
    logger.info(f"  建议: {eval_data['recommendation']}")

    # 4. 渲染
    logger.info(f"生成仪表盘: {args.output}")
    dashboard.render_dashboard(eval_data, args.output)

    logger.info("=" * 60)
    logger.info("✓ 完成")
    logger.info(f"  仪表盘路径: {os.path.abspath(args.output)}")
    logger.info("  双击或拖入浏览器打开即可查看")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
