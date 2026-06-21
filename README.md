# AI 泡沫监控仪表盘 v2.0

> 个人投资者的 0-100 量化指数 + 5 大核心指标

## 部署到 GitHub Pages

### 第一次部署（5 步）

1. **创建 GitHub repo**
   - 登录 GitHub
   - 新建 repository，例如 `ai-bubble-dashboard` (Public)
   - 不要勾选 "Add README"（我们已有）

2. **把项目 push 到 GitHub**
   ```bash
   cd E:\自媒体\B站\视频封面\ai_bubble_dashboard
   git init
   git add .
   git commit -m "feat: initial dashboard"
   git branch -M main
   git remote add origin https://github.com/<你的用户名>/ai-bubble-dashboard.git
   git push -u origin main
   ```

3. **启用 GitHub Pages**
   - 进入 repo 页面 → Settings → Pages
   - Source: `Deploy from a branch`
   - Branch: `main` / `(root)` （或 `/docs` 如果你想把仪表盘放子目录）
   - 等待 1-2 分钟，GitHub 会给你一个 URL：`https://<用户名>.github.io/ai-bubble-dashboard/data/dashboard.html`
   - 这就是你可以随时打开的链接

4. **启用 GitHub Actions 自动更新**
   - 已经在 `.github/workflows/update.yml` 写好
   - 每周一 09:00 UTC（北京时间 17:00）自动跑 `update.py`
   - 也可手动触发: repo 页面 → Actions → "Update AI Bubble Dashboard" → Run workflow

5. **首次拉取历史数据**
   - 第一次部署时，repo 没有历史数据（Yale Shiller 等）
   - Actions 会自动跑 `backfill_*` 拉数据
   - 之后每周自动更新

### 本地更新

如果你想在本地更新（更快、可以看实时 log）：

```cmd
cd E:\自媒体\B站\视频封面\ai_bubble_dashboard
run.bat
```

一键：跑 Python + 重新渲染 + 自动打开浏览器。

或者手动：
```cmd
D:\python.exe update.py
start data\dashboard.html
```

### 平时使用

**访问仪表盘**：`https://<用户名>.github.io/ai-bubble-dashboard/data/dashboard.html`

**最新数据**会在每周一 17:00（北京时间）自动更新。

**手工补数据**（如果某个指标缺失）：
```cmd
D:\python.exe manual_input.py finra-margin 2025-09 815000
D:\python.exe manual_input.py capex 2026Q1 142.5 9.2
D:\python.exe manual_input.py list
```

## 当前系统

| 指标 | 权重 | 数据源 | 频率 |
|---|---|---|---|
| Mag 4 Capex / AI 收入 | 25% | SEC EDGAR XBRL | 季度（自动） |
| Shiller CAPE | 20% | Yale Shiller 数据 | 每日（自动） |
| Mag 7 集中度 | 15% | yfinance | 每日（自动） |
| SOX 半导体指数 月环比 | 20% | yfinance ^SOX + 本地缓存 | 月（自动 + 缓存） |
| Mag 7 内部人 6M 卖/买 | 20% | OpenInsider / Form 4 | 月（自动） |

## 文件结构

```
ai_bubble_dashboard/
├── README.md
├── requirements.txt
├── config.py                  # 阈值 + 权重
├── db.py                      # SQLite
├── index.py                   # 0-100 指数
├── dashboard.py               # 仪表盘渲染
├── update.py                  # 主入口
├── manual_input.py            # 手工录入
├── seed_data.py               # 示例数据
├── seed_fred_history.py       # FINRA 历史
├── seed_shiller_2024.py       # Shiller 2024+ 估算
├── seed_sox_history.py        # SOX 半导体历史
├── run.bat                    # 本地一键更新
├── fetchers/
│   ├── shiller_mag7.py
│   ├── capex_revenue.py
│   ├── aws_spot_gpu.py        # SOX 半导体
│   ├── insider.py
│   ├── insider_margin.py      # FINRA / Trading Economics
│   └── historical.py          # Yale + 标普历史
├── data/
│   ├── ai_bubble.db           # SQLite
│   ├── dashboard.html         # 仪表盘（GitHub Pages 主入口）
│   ├── historical/            # Yale xls 缓存
│   └── manual/                # 手工录入 + SOX 缓存
├── templates/
│   └── dashboard.html
├── logs/
└── .github/
    └── workflows/
        └── update.yml         # GitHub Actions
```

## 决策规则

- **3 个红灯亮** → 分批减仓
- **5 个红灯亮** → 清仓
- **指数 ≥ 80** → 极度泡沫区
- **指数 ≤ 20** → 恐慌/低估区

## 免责声明

本仪表盘仅供个人研究与决策参考，**不构成投资建议**。所有数据基于公开源与简化模型，存在数据延迟、采集错误、模型局限等风险。投资决策请结合自身风险承受能力独立判断。
