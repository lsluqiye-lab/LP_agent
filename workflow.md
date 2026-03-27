---

## 阶段一：启动初始化（main.py 执行一次）
```plain

bash start.sh
  ↓
加载环境变量（LongPort API、DeepSeek Key、飞书 Webhook、代理）
  ↓
python3 main.py
  ↓
┌──────────────────────────────────────────────────┐
│  1. 加载配置 AppConfig.from_env("deepseek")       │
│  2. 创建 DeepSeek LLM 实例                        │
│  3. 创建工具注册表，注册三类工具：                    │
│     ├─ 交易工具 (8个): get_market_status,          │
│     │   get_positions, get_account_balance,        │
│     │   get_today_orders, get_history_orders,      │
│     │   get_quote, buy_stock, sell_stock           │
│     ├─ 行情数据工具 (5个): get_technical_analysis,  │  ← 新增
│     │   get_kline, get_capital_flow,               │
│     │   get_fundamentals, get_market_overview      │
│     └─ 搜索工具 (6个): search_stock_news,          │
│         search_market_sentiment, 等                │
│  4. 创建飞书通知器                                  │
│  5. 创建 ReAct 智能体（注入 system prompt）          │
└──────────────────────────────────────────────────┘
```

## 阶段二：主循环（每轮重复执行）
```plain
while True:
  ↓
获取当前时间（美东 + 北京双显示）
  ↓
┌─ 不在交易时段（美东 8:30 前 or 17:00 后 or 周末假日）？
│   → 日志: "当前不在交易时段，跳过执行"
│   → 休眠 10 秒，回到循环顶部
│
└─ 在交易时段（美东 8:30~17:00 工作日）？
    → 进入 agent.run()，启动 ReAct 推理
    → 推理结束后休眠 300 秒（5分钟），回到循环顶部
```

## 阶段三：ReAct 推理（agent.run()，每轮交易时段触发一次）
这是核心逻辑，分为 预执行 → 多轮推理 → 输出 三步：

### Step 0 — 预执行工具（自动收集背景信息）
在 LLM 开口说话之前，系统先自动调用 8 个工具把结果打包成文本注入对话上下文：

```plain
预执行（不需要 LLM 决策，直接调用）:
  ├─ get_market_status      → "盘中/盘前/盘后/休市"
  ├─ get_positions           → 当前持仓列表
  ├─ get_account_balance     → 总资产、可用现金
  ├─ get_today_orders        → 今日订单
  ├─ get_history_orders      → 近7天历史订单
  ├─ search_macro_economics  → 宏观经济（美联储/通胀等）
  ├─ search_earnings_calendar → 近期财报日历
  └─ search_geopolitical_news → 地缘政治新闻
                ↓
  打包为一条 "背景信息" 消息注入对话
```

注意：这 8 个工具的结果注入后，它们就从 LLM 可调用的工具列表中移除了，避免 LLM 重复调用。

### Step 1~N — ReAct 循环（LLM 思考 + 调用工具，最多 10 轮）
LLM 拿到背景信息后，按照 system prompt 的中长线策略进行推理：

```plain
┌─────────────────────────────────────────────────────────┐
│  LLM 收到的上下文:                                        │
│    • system prompt（中长线策略 + 风控规则 + 工作流程）        │
│    • 预执行结果（市场状态/持仓/账户/订单/宏观信息）            │
│    • 可调用的剩余 11 个工具                                 │
│                                                           │
│  LLM 思考后决定调用工具:                                     │
│                                                           │
│  迭代1: 持仓巡检                                            │
│    → 并行调用 get_technical_analysis(XLU)                  │
│    → 并行调用 get_technical_analysis(XLE)                  │
│    → 并行调用 get_technical_analysis(TLT) ...              │
│    → 并行调用 get_market_overview()                        │
│    ← 收到结果：每只持仓的 SMA/RSI/RS/止损价位 + 大盘状况     │
│                                                           │
│  迭代2: 深入分析（如需要）                                   │
│    → 调用 get_fundamentals(AAPL,NVDA,MSFT)               │
│    → 调用 get_capital_flow(某个感兴趣的股票)                 │
│    → 调用 search_stock_news(某个持仓股)                     │
│    ← 收到结果                                              │
│                                                           │
│  迭代3: 做出最终决策                                        │
│    → 如需交易: 调用 buy_stock / sell_stock                 │
│    → 如不操作: 直接输出 HOLD + 分析理由                     │
│                                                           │
│  ...直到 LLM 不再调用工具（任务完成）或达到 10 轮上限         │
└─────────────────────────────────────────────────────────┘
```

### Step 最终 — 输出与通知
```plain
LLM 输出最终报告（按格式）:
  【市场环境】SPY/QQQ 均跌破 SMA50，大盘横盘弱势
  【持仓巡检】XLU: $82.3, 止损$75.7, HOLD ...
  【操作决策】HOLD — 无信号触发
  【风控计算】无交易
  【下轮关注】GDX 距 -10% 止损仅剩 3%
        ↓
如果本轮执行了 buy_stock 或 sell_stock:
  → 通过飞书推送交易通知
        ↓
日志记录结果
        ↓
休眠 5 分钟 → 回到主循环
```

---

工具总览（当前 19 个）

| **类别** | **工具** | **数据来源** | **用途** |
| :---: | :---: | :---: | :---: |
| 交易 | get_market_status | 本地计算 | 判断交易时段 |
| 交易 | get_positions | LongPort Trade API | 当前持仓 |
| 交易 | get_account_balance | LongPort Trade API | 账户余额 |
| 交易 | get_today_orders | LongPort Trade API | 今日订单 |
| 交易 | get_history_orders | LongPort Trade API | 历史订单 |
| 交易 | get_quote | LongPort Quote API | 实时报价 |
| 交易 | buy_stock | LongPort Trade API | 买入下单 |
| 交易 | sell_stock | LongPort Trade API | 卖出下单 |
| 行情 | get_technical_analysis | LongPort Quote API | 核心：SMA/RSI/RS/Stage2 一站式分析 |
| 行情 | get_kline | LongPort Quote API | 原始K线数据 |
| 行情 | get_capital_flow | LongPort Quote API | 主力资金流向 |
| 行情 | get_fundamentals | LongPort Quote API | PE/PB/市值/涨跌幅 |
| 行情 | get_market_overview | LongPort Quote API | 大盘 SPY/QQQ 技术面 |
| 搜索 | search_stock_news | Gemini + Google | 个股新闻 |
| 搜索 | search_market_sentiment | Gemini + Google | 社交舆情 |
| 搜索 | search_financial_analysis | Gemini + Google | 分析师评级 |
| 搜索 | search_macro_economics | Gemini + Google | 宏观经济 |
| 搜索 | search_earnings_calendar | Gemini + Google | 财报日历 |
| 搜索 | search_geopolitical_news | Gemini + Google | 地缘政治 |


新增的 5 个行情数据工具（加粗部分）是直接从长桥交易所拿数据，不依赖 Gemini，所以即使搜索工具因为代理问题不可用，agent 的技术面分析能力完全不受影响。搜索工具现在更多是"锦上添花"——用来确认基本面和新闻面。

