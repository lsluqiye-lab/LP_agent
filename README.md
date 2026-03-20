# LP-Agent v2.0

基于 LLM + ReAct 框架的美股自动交易智能体，通过 LongPort OpenAPI 执行实盘交易。系统采用 **Bear-Case-First** 决策哲学——每次买入前先穷尽风险因素，确认风险可控后才果断行动。

---

## 项目概述

LP-Agent 是一个全自动的美股中长线趋势交易系统。它在交易时段（美东 8:30–17:00）持续运行，每轮执行五个阶段：数据收集、宏观风控评分、候选标的筛选、ReAct 推理决策与交易执行、收盘复盘。所有交易通过 LongPort 券商 API 下单，决策过程完整记录，关键操作实时推送至飞书群。

### 核心特性

- **固定标的池**：仅交易精选的 10 只美股（NVDA、TSM、MSFT、VRT、CEG、LLY、ISRG、SPGI、MA、GE），聚焦 AI 算力、半导体、云计算、医疗、金融等赛道龙头
- **宏观风控引擎**：6 维度加权评分（0–100 分），动态划分 4 级风险状态（LOCKDOWN / CAUTIOUS / NORMAL / FAVORABLE），自动调节仓位上限和交易权限
- **Bear-Case-First 决策**：LLM 对每只候选标的必须先识别风险因素（Bear Case），再评估正面信号（Bull Case），最后做出买入/持有/卖出决策
- **多阶段深度分析**：ReAct 推理强制执行 4 阶段工作流——技术面分析 → 基本面+资金面验证 → 消息面+舆情搜索 → 综合研判决策，杜绝仅凭单一维度数据草率决策
- **多维度技术分析**：趋势（SMA/ADX/Stage2）、动量（RSI/MACD/StochRSI）、波动（布林带/ATR）、量价（OBV/量比）、相对强度（vs SPY）、形态信号，共 6 大维度
- **多 LLM 支持**：支持 DeepSeek、Google Gemini 作为推理引擎，通过环境变量一键切换
- **实时信息搜索**：通过 Gemini + Google Search 获取宏观经济、财报日历、地缘政治、个股新闻等实时信息
- **每日自动复盘**：收盘后（美东 16:30）自动生成结构化复盘报告，评估决策质量和风控系统表现
- **飞书通知**：交易执行和复盘报告自动推送至飞书群聊

---

## 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                     main.py (主循环)                      │
│                                                          │
│  Phase 1: 数据收集 ──→ Phase 2: 风控评分 ──→ Phase 2.5:  │
│  (账户/行情/搜索)    (MacroRiskManager)    候选标的筛选   │
│                                                          │
│  Phase 3&4: ReAct 推理 + 交易执行 ──→ Phase 5: 每日复盘  │
│  (ReActAgent + LLM + Tools)            (ReviewAgent)     │
└─────────────────────────────────────────────────────────┘
         │                  │                  │
    ┌────┴────┐       ┌────┴────┐       ┌────┴────┐
    │  tools  │       │   llm   │       │  agent  │
    ├─────────┤       ├─────────┤       ├─────────┤
    │trading  │       │deepseek │       │react    │
    │market   │       │gemini   │       │review   │
    │search   │       │base     │       │risk_mgr │
    └─────────┘       └─────────┘       └─────────┘
         │                                    │
    ┌────┴────┐                         ┌────┴────┐
    │LongPort │                         │  data   │
    │OpenAPI  │                         │trade_log│
    └─────────┘                         └─────────┘
```

---

## 项目结构

```
LP_agent_0318/
├── main.py                 # v2.0 主入口，五阶段执行架构
├── LP-Agent.py             # v1.0 原始版本（单文件，已弃用）
├── config.py               # 配置管理（标的池、风控参数、LLM、复盘等）
├── logger.py               # 日志模块（按日轮转）
├── agent/
│   ├── react.py            # ReAct 智能体（Bear-Case-First 推理循环）
│   ├── risk_manager.py     # 宏观风控评分引擎（6维度加权）
│   └── review.py           # 每日复盘智能体
├── tools/
│   ├── base.py             # 工具抽象基类与注册表
│   ├── trading.py          # 交易工具（8个：持仓/余额/下单/报价等）
│   ├── market_data.py      # 行情数据工具（6个：技术分析/扫描/K线/资金流等）
│   └── search.py           # 搜索工具（6个：新闻/舆情/分析师/宏观/财报/地缘）
├── llm/
│   ├── base.py             # LLM 抽象基类
│   ├── deepseek.py         # DeepSeek 实现（OpenAI 兼容 API）
│   └── gemini.py           # Google Gemini 实现（官方 SDK）
├── notification/
│   └── feishu.py           # 飞书 Webhook 消息推送
├── data/
│   ├── trade_logger.py     # 交易日志记录器（JSON 文件存储）
│   └── logs/               # 每日交易日志、复盘报告存储目录
├── requirements.txt        # Python 依赖
├── Dockerfile              # Docker 镜像构建（v1.0 版本）
├── start.sh                # 启动脚本（设置环境变量 + 后台运行）
├── build.sh                # Docker 构建脚本
├── workflow.md             # 工作流程文档
└── longport_openapi.md     # LongPort SDK 参考文档
```

---

## 执行流程

### 五阶段循环

每轮交易时段内，系统按以下顺序执行：

**Phase 1 — 数据收集**：自动调用 9 个预执行工具，收集账户持仓、余额、订单、大盘环境（SPY/QQQ 技术面 + LongPort 市场温度）、标的池批量扫描、宏观经济/财报日历/地缘政治新闻。这些数据既作为风控引擎的输入，也作为 LLM 推理的上下文。

**Phase 2 — 风控评分**：MacroRiskManager 基于 Phase 1 的数据，从市场温度（25%）、SPY 技术面（25%）、RSI 广度（15%）、资金流向（15%）、市场情绪（10%）、波动率（10%）六个维度计算加权评分。根据评分自动划分风险级别并设定交易约束：

| 风险级别 | 评分区间 | 行为约束 |
|----------|----------|----------|
| LOCKDOWN | 0–30 | 禁止一切新建仓，考虑减仓至 30% 以下 |
| CAUTIOUS | 30–50 | 仅允许减仓或持有，总仓位限制 50% |
| NORMAL | 50–70 | 正常交易，仓位按评分动态调整倍率 |
| FAVORABLE | 70–100 | 环境良好，可积极建仓 |

**Phase 2.5 — 候选标的筛选**：从扫描结果和当前持仓中提取需要逐一分析的标的。所有持仓标的必须进行止损/止盈巡检；在风控允许买入时，具有 Stage2 上升趋势、技术信号、或近期强势的标的被选入买入候选。

**Phase 3 & 4 — ReAct 多阶段推理与执行**：ReAct Agent 接收完整上下文（风控状态、预执行数据、候选清单），通过 LLM 进行多轮推理，强制执行 4 阶段分析工作流：

1. **技术面分析**：对每只候选标的调用 `get_technical_analysis`，获取 6 维度技术指标
2. **基本面+资金面验证**：调用 `get_fundamentals` 获取估值数据，调用 `get_capital_flow` 获取主力资金流向，验证技术信号的可靠性
3. **消息面+舆情搜索**：调用 `search_stock_news` 搜索个股新闻，调用 `search_financial_analysis` 获取分析师评级，调用 `search_market_sentiment` 了解散户情绪
4. **综合研判决策**：汇总所有维度数据，按 Bear-Case-First 框架（先风险后机会）对每只标的输出最终决策。需要交易时直接调用 `buy_stock` / `sell_stock` 完成下单

**Phase 5 — 每日复盘**：美东 16:30 后自动触发（每天仅一次），ReviewAgent 汇总当日所有风控评分、决策记录、交易记录和错误日志，调用 LLM 生成结构化复盘报告，包括决策质量评估、风控系统评估、改进建议等，并推送至飞书。

### 交易时段与休眠

- **交易时段**（美东 8:30–17:00 工作日）：每 10 分钟执行一轮
- **非交易时段**：每 10 秒轮询检测（等待进入交易时段）
- 自动识别 NYSE 假日和周末，假日与周末不执行任何交易逻辑

---

## 工具总览

系统共注册 **20 个工具**，分为三类：

### 交易工具（8 个）

| 工具 | 数据来源 | 说明 |
|------|----------|------|
| `get_market_status` | 本地计算 | 判断当前交易时段（盘前/盘中/盘后/休市） |
| `get_positions` | LongPort Trade API | 获取当前 USD 持仓列表 |
| `get_account_balance` | LongPort Trade API | 获取账户总资产与可用现金 |
| `get_today_orders` | LongPort Trade API | 获取今日订单记录 |
| `get_history_orders` | LongPort Trade API | 获取近 N 天历史订单 |
| `get_quote` | LongPort Quote API | 获取个股实时报价 |
| `buy_stock` | LongPort Trade API | 买入下单（支持市价单 MO / 限价单 LO） |
| `sell_stock` | LongPort Trade API | 卖出下单（支持市价单 MO / 限价单 LO） |

### 行情数据工具（6 个）

| 工具 | 数据来源 | 说明 |
|------|----------|------|
| `get_technical_analysis` | LongPort Quote API | 个股完整技术分析报告（6 维度指标 + 信号评分） |
| `scan_watchlist` | LongPort Quote API | 一次性扫描全部 10 只标的池股票的关键技术指标 |
| `get_kline` | LongPort Quote API | 获取 K 线数据（日/周/月/60 分钟线） |
| `get_capital_flow` | LongPort Quote API | 资金流向分析（大/中/小单分布 + 主力方向） |
| `get_fundamentals` | LongPort Quote API | 基本面指标（PE/PB/市值/多周期涨跌幅），支持批量查询 |
| `get_market_overview` | LongPort Quote API | 大盘环境分析（SPY/QQQ 技术面 + LongPort 市场温度） |

### 搜索工具（6 个）

| 工具 | 数据来源 | 说明 |
|------|----------|------|
| `search_stock_news` | Gemini + Google Search | 个股新闻与重大事件 |
| `search_market_sentiment` | Gemini + Google Search | 社交媒体舆情分析（Reddit/Twitter/StockTwits） |
| `search_financial_analysis` | Gemini + Google Search | 分析师评级、目标价、机构持仓 |
| `search_macro_economics` | Gemini + Google Search | 宏观经济信息（带 1 小时缓存） |
| `search_earnings_calendar` | Gemini + Google Search | 财报日历与业绩数据（带 2 小时缓存） |
| `search_geopolitical_news` | Gemini + Google Search | 地缘政治新闻与市场影响分析（带 1 小时缓存） |

> 行情数据工具直接通过 LongPort API 获取，不依赖搜索引擎。即使搜索工具因网络问题不可用，核心技术分析能力完全不受影响。搜索工具更多是"锦上添花"——用来确认基本面和新闻面。

---

## 买入与卖出策略

### 买入条件（分级制）

**必要条件**（缺一不可）：

1. Stage 2 上升趋势确认（股价 > SMA50 > SMA200）
2. 风控评分允许买入（NORMAL 或 FAVORABLE）
3. 周线 RSI(14) 在 35–80 之间

**加分条件**（满足 ≥3 项即可建仓）：

- ADX > 25 且方向多头（趋势有强度）
- MACD 柱状图为正 或 近期金叉
- 布林带 %B > 0.3
- OBV 无看空背离
- 近期成交量 ≥ 1.2 × 50 日均量
- 20 日收益跑赢 SPY
- signal_summary.score ≥ 3
- 主力资金净流入（`get_capital_flow` 显示 large_net > 0）
- 分析师评级以买入/增持为主（`search_financial_analysis`）

**建仓规模与加分条件挂钩**：

| 加分项数 | 建仓规模 | 说明 |
|----------|----------|------|
| 3 项 | 计划仓位的 40% | 试探性建仓 |
| 4–5 项 | 计划仓位的 70% | 标准建仓 |
| 6 项以上 | 计划仓位的 100% | 满额建仓 |

**仓位计算公式**：

```
预期止损% = max(8%, 2 × ATR%)
计划仓位金额 = min(可用现金 × 单笔上限% × 风控倍率, 总资产 × 1.5% / 预期止损%)
实际金额 = 计划仓位金额 × 建仓规模比例
交易数量 = floor(实际金额 / 当前股价)
```

### 卖出规则

**硬性止损**：

- 股价从买入价下跌 8%–12% → 立即止损
- 股价有效跌破 SMA50（连续 2–3 日收盘在下方）
- 单笔亏损 > 总资产的 1.5% → 强制止损
- ATR 止损：跌破买入价 - 2×ATR

**移动止盈**：

- 盈利 > 20% 后，止盈线上移至 SMA20
- 盈利 > 50% 后，止盈线上移至 SMA50
- 跌破止盈线 → 卖出

---

## 快速开始

### 环境要求

- Python 3.10+
- LongPort 券商账户（需要 App Key / App Secret / Access Token）
- LLM API Key（DeepSeek 或 Google Gemini 至少一个）
- （可选）飞书群聊自定义机器人 Webhook URL

### 安装依赖

```bash
pip install -r requirements.txt
```

主要依赖：`longport`、`openai`、`google-genai`、`holidays`、`pytz`、`requests`、`dashscope`、`matplotlib`

### 配置环境变量

```bash
# ── LongPort 券商 API ──
export LONGPORT_APP_KEY="your_app_key"
export LONGPORT_APP_SECRET="your_app_secret"
export LONGPORT_ACCESS_TOKEN="your_access_token"

# ── LLM 配置（二选一）──
export LLM_PROVIDER=deepseek              # 或 gemini
export DEEPSEEK_API_KEY="your_key"        # 使用 DeepSeek 时配置
export GEMINI_API_KEY="your_key"          # 使用 Gemini 时配置（同时用于搜索工具）

# ── 飞书通知（可选）──
export FEISHU_WEBHOOK_URL="your_webhook_url"

# ── 代理配置（如需翻墙访问 Gemini API）──
# export http_proxy='http://127.0.0.1:7890'
# export https_proxy='http://127.0.0.1:7890'
```

### 启动运行

```bash
# 前台运行（调试用）
python3 main.py

# 后台运行（生产推荐）
nohup python3 -u main.py >> agent.log 2>&1 &
```

或使用启动脚本（需先编辑 `start.sh` 填入自己的 API Key）：

```bash
bash start.sh
```

查看日志：

```bash
tail -f agent.log           # 实时输出
tail -f logs/trading_agent.log  # 结构化日志
```

### Docker 部署

```bash
docker build -t lp-agent .
docker run -d \
  -e LONGPORT_APP_KEY=xxx \
  -e LONGPORT_APP_SECRET=xxx \
  -e LONGPORT_ACCESS_TOKEN=xxx \
  -e DEEPSEEK_API_KEY=xxx \
  lp-agent
```

> 注意：当前 Dockerfile 针对 v1.0 单文件版本（LP-Agent.py）构建，v2.0 多模块版本的容器化部署需自行调整 Dockerfile。

---

## 配置参数

所有配置通过环境变量管理，在 `config.py` 中统一定义：

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| LLM 提供商 | `LLM_PROVIDER` | `deepseek` | 可选 `deepseek` / `gemini` |
| DeepSeek 模型 | `DEEPSEEK_MODEL` | `deepseek-chat` | DeepSeek 模型名称 |
| Gemini 模型 | `GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini 模型名称 |
| 最大迭代轮数 | `AGENT_MAX_ITERATIONS` | `10` | ReAct 循环最大步数 |
| 交易时段休眠 | `SLEEP_INTERVAL_TRADING` | `600` | 每轮间隔（秒） |
| 非交易时段休眠 | `SLEEP_INTERVAL_NON_TRADING` | `10` | 非交易时段轮询间隔（秒） |
| 风控-封锁阈值 | `RISK_SCORE_LOCKDOWN` | `30` | 低于此分禁止新建仓 |
| 风控-谨慎阈值 | `RISK_SCORE_CAUTIOUS` | `50` | 低于此分仅允许减仓 |
| 风控-正常阈值 | `RISK_SCORE_NORMAL` | `70` | 高于此分可正常建仓 |
| 复盘开关 | `REVIEW_ENABLED` | `true` | 是否启用每日复盘 |
| 复盘时间 | `REVIEW_HOUR` / `REVIEW_MINUTE` | `16` / `30` | 美东时间触发复盘 |
| 日志目录 | `LOG_DIR` | `logs` | 运行日志存储目录 |
| 日志级别 | `LOG_LEVEL` | `INFO` | 日志级别 |

---

## 日志与数据存储

- **运行日志**：`logs/trading_agent.log`（按日轮转，保留 30 天），格式为 `时间 | 级别 | 模块 | 内容`
- **交易日志**：`data/logs/trade_log_YYYY-MM-DD.json`（每日一文件），包含以下内容：
  - `risk_scores`：每轮风控评分（分数、级别、6 维度明细）
  - `decisions`：每次交易决策（标的、动作、推理过程、是否通过审批）
  - `trades`：实际执行的交易记录（标的、方向、数量、价格、理由）
  - `errors`：错误信息
  - `summary`：每日复盘总结

---

## 版本演进

**v1.0**（LP-Agent.py）：单文件架构，通过阿里云百炼 Application API 调用 LLM，直接解析 JSON 指令下单，无风控系统和技术分析能力。适合作为概念验证。

**v2.0**（main.py，当前版本）：完整重构为模块化架构，主要改进：

- 五阶段执行流程（数据收集 → 风控评分 → 候选筛选 → ReAct 推理 → 每日复盘）
- 宏观风控引擎（6 维度加权评分，4 级风险状态，动态仓位约束）
- Bear-Case-First 决策框架（先风险后机会）
- 20 个专业工具（交易 8 + 行情 6 + 搜索 6）
- 多 LLM 支持（DeepSeek / Gemini）
- 每日自动复盘系统
- 飞书实时通知
- 结构化 JSON 交易日志

---

## 免责声明

本项目仅供学习和研究目的。股票交易存在风险，使用本系统进行实盘交易所产生的一切损失由用户自行承担。请在充分了解风险的前提下谨慎使用。
