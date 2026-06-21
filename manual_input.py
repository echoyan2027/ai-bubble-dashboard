"""
手工录入工具 — 财报披露后录入数据

用法:
    python manual_input.py capex 2026Q1 142.5 9.2
        → 录入 2026Q1 hyperscaler capex 142.5B USD, AI 收入 9.2B USD

    python manual_input.py mag7-quarter 2026Q1
        → 录入 Mag 7 各家 Capex 季度数据

    python manual_input.py ai-revenue-quarter 2026Q1 9.2
        → 仅更新 AI 收入

    python manual_input.py finra-margin 2026-05 850000000000
        → 录入 FINRA 散户保证金月度值（美元）

    python manual_input.py list
        → 查看所有手工录入数据
"""
import sys
import os
import json
import argparse
import logging
from datetime import date
import db
from fetchers.capex_revenue import (
    load_quarterly_data, save_quarterly_data, add_quarterly_record,
)

logger = logging.getLogger(__name__)

MANUAL_DIR = "data/manual"
QUARTERLY_FILE = os.path.join(MANUAL_DIR, "capex_revenue_quarterly.json")
MARGIN_FILE = os.path.join(MANUAL_DIR, "finra_margin.json")


def cmd_capex(period, capex_b, revenue_b, source):
    """录入 Capex/收入"""
    add_quarterly_record(period, float(capex_b), float(revenue_b), source or "")
    print(f"✓ {period}: capex={capex_b}B, revenue={revenue_b}B, "
          f"ratio={float(capex_b) / float(revenue_b):.2f}x")


def cmd_mag7_quarter(period, *amounts):
    """
    录入 Mag 7 四家 (MSFT/AMZN/GOOG/META) 的季度 capex (B USD)
    例: 2026Q1 MSFT=24 AMZN=28 GOOG=25 META=18
    """
    if len(amounts) != 4:
        print("ERROR: 需要 4 个数: MSFT AMZN GOOG META (B USD)", file=sys.stderr)
        return
    total = sum(float(x) for x in amounts)
    print(f"Mag 7 季度 capex 合计: {total:.2f} B USD")
    # 提示下一步：录入收入
    print(f"接下来请输入 AI 收入(全行业, B USD):")
    try:
        revenue = float(input("  revenue_b = ").strip())
    except (ValueError, EOFError):
        print("已取消")
        return
    add_quarterly_record(period, total, revenue, "mag7_4_sum")
    print(f"✓ {period}: capex={total:.2f}B, revenue={revenue}B, "
          f"ratio={total / revenue:.2f}x")


def cmd_finra_margin(period, amount_usd):
    """录入 FINRA 散户保证金"""
    os.makedirs(MANUAL_DIR, exist_ok=True)
    data = []
    if os.path.exists(MARGIN_FILE):
        with open(MARGIN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    data = [d for d in data if d.get("period") != period]
    data.append({
        "period": period,
        "amount_usd": float(amount_usd),
        "added_at": date.today().isoformat(),
    })
    data.sort(key=lambda x: x["period"])
    with open(MARGIN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 计算 3 个月累增
    growth = 0.0
    if len(data) >= 4:
        latest = data[-1]
        three_ago = data[-4]
        growth = (latest["amount_usd"] - three_ago["amount_usd"]) \
                 / three_ago["amount_usd"] * 100

    # 写入 DB
    from datetime import datetime
    obs_date = period + "-01" if len(period) == 7 else period
    db.insert_data("retail_margin_debt", float(amount_usd), obs_date=obs_date,
                   source="manual", raw_payload={"period": period})
    db.insert_data("retail_margin_growth", growth, obs_date=obs_date,
                   source="manual", raw_payload={"period": period})
    print(f"✓ {period}: amount=${float(amount_usd):,.0f}, 3M growth={growth:.2f}%")


def cmd_list():
    """列出所有手工录入数据"""
    print("=" * 60)
    print("Capex/Revenue 季度数据:")
    print("=" * 60)
    records = load_quarterly_data()
    if records:
        for r in records:
            print(f"  {r['period']:>8}  capex={r['hyperscaler_capex_b']:>7.2f}B  "
                  f"revenue={r['ai_revenue_b']:>7.2f}B  "
                  f"ratio={r['hyperscaler_capex_b'] / r['ai_revenue_b']:>5.2f}x")
    else:
        print("  (empty)")

    print()
    print("=" * 60)
    print("FINRA 散户保证金:")
    print("=" * 60)
    if os.path.exists(MARGIN_FILE):
        with open(MARGIN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for d in data:
            print(f"  {d['period']:>8}  ${d['amount_usd']:,.0f}")
    else:
        print("  (empty)")


def main():
    parser = argparse.ArgumentParser(
        description="AI 泡沫仪表盘 - 手工录入工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    p1 = sub.add_parser("capex", help="录入 Capex + AI 收入")
    p1.add_argument("period", help="e.g. 2026Q1")
    p1.add_argument("capex_b", help="Hyperscaler 季度 Capex 合计 (B USD)")
    p1.add_argument("revenue_b", help="AI 相关季度收入 (B USD)")
    p1.add_argument("--source", default="", help="数据来源备注")

    p2 = sub.add_parser("mag7-quarter", help="录入 Mag 7 四家季度 capex (MSFT AMZN GOOG META)")
    p2.add_argument("period", help="e.g. 2026Q1")
    p2.add_argument("msft", type=float)
    p2.add_argument("amzn", type=float)
    p2.add_argument("goog", type=float)
    p2.add_argument("meta", type=float)

    p3 = sub.add_parser("finra-margin", help="录入 FINRA 散户保证金月度值")
    p3.add_argument("period", help="e.g. 2026-05")
    p3.add_argument("amount_usd", help="美元")

    sub.add_parser("list", help="列出所有手工录入数据")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    if args.cmd == "capex":
        cmd_capex(args.period, args.capex_b, args.revenue_b, args.source)
    elif args.cmd == "mag7-quarter":
        cmd_mag7_quarter(args.period, args.msft, args.amzn, args.goog, args.meta)
    elif args.cmd == "finra-margin":
        cmd_finra_margin(args.period, args.amount_usd)
    elif args.cmd == "list":
        cmd_list()


if __name__ == "__main__":
    main()
