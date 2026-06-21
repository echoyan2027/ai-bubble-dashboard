"""
清掉已弃用指标的 DB 数据 + 重算 SOX 月环比
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

# 清掉已弃用指标
with db.get_conn() as conn:
    for k in ['retail_margin_growth', 'retail_margin_debt', 'capex_vs_revenue_growth']:
        cnt = conn.execute("DELETE FROM metric_data WHERE metric_key = ?", (k,)).rowcount
        print(f"  清除 {k}: {cnt} 条")

# 删除 metric_meta 中的已弃用
with db.get_conn() as conn:
    for k in ['retail_margin_growth', 'capex_vs_revenue_growth']:
        cnt = conn.execute("DELETE FROM metric_meta WHERE metric_key = ?", (k,)).rowcount
        print(f"  清除 meta {k}: {cnt} 条")

# 重算 SOX 月环比历史（基于已存的 semiconductor_index 月度收盘价）
import json
history = db.get_history("semiconductor_index", limit=200)
# 去重 (相同 obs_date 保留最新)
seen = {}
for h in history:
    if h["obs_date"] not in seen or h["id"] > seen[h["obs_date"]]["id"]:
        seen[h["obs_date"]] = h
unique_history = sorted(seen.values(), key=lambda x: x["obs_date"])

print(f"\n  SOX 月度数据: {len(unique_history)} 条")

# 算月环比历史
with db.get_conn() as conn:
    # 清掉旧的
    conn.execute("DELETE FROM metric_data WHERE metric_key = 'semiconductor_proxy'")
    for i in range(1, len(unique_history)):
        prev = unique_history[i-1]
        curr = unique_history[i]
        mom_pct = (curr["value"] - prev["value"]) / prev["value"] * 100
        conn.execute(
            """INSERT OR REPLACE INTO metric_data
               (metric_key, obs_date, obs_period, value, raw_payload, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("semiconductor_proxy", curr["obs_date"], f"MoM-{curr['obs_date'][:7]}",
             mom_pct, json.dumps({"close": curr["value"], "prev_close": prev["value"]}),
             "computed_from_history"),
        )

# 验证
print("\n=== SOX 月环比历史 (近 12 个月) ===")
mom_history = db.get_history("semiconductor_proxy", limit=15)
mom_history.reverse()
for r in mom_history:
    print(f"  {r['obs_date']}: {r['value']:+.2f}%")
