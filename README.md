# LP-Agent v3.0

基于 **Strategic Multi-Agent** 架构的美股自动交易智能体。系统模拟对冲基金运行模式，由 **CIO (首席投资官)** 决策大脑统筹多个**领域专家智能体**，通过 LongPort OpenAPI 执行实盘交易。

系统核心哲学：**Bear-Case-First (风险优先)** —— 所有的买入必须建立在对风险因素的彻底调查和证伪之上。

---

## 🚀 决策漏斗：如何选取标的？

系统并不是盲目扫描，而是通过一个**三层过滤漏斗**，从数千只股票中锁定最具爆发力的目标：

### 层级 1：动态标的池 (Daily Warm-up)
*   **触发时间**：每日美东时间开盘前。
*   **逻辑**：调用 `gemini-3-flash` 结合实时搜索，扫描市场热度、成交量异动及重大新闻催化剂。
*   **结果**：动态生成 10-15 只核心观察名单（包含 NVDA, MSFT 等常驻标的），确保系统始终聚焦在“市场风口”上。

### 层级 2：定量信号筛选 (Phase 2.5 - The Filter)
*   **触发时间**：每 10 分钟循环。
*   **核心逻辑**：
    1.  **持仓巡检 (Mandatory)**：所有当前持仓标的一键送审，强制检查止损位（8% 或跌破 SMA50）和止盈机会。
    2.  **技术信号筛选**：从未持仓标的中提取符合以下条件的“候选人”：
        *   **Minervini Stage 2**：确认股价处于主升浪（股价 > 50日均线 > 200日均线）。
        *   **动量爆发**：出现 MACD 金叉、RSI 处于 40-70 健康区、或 20 日收益显著跑赢大盘。
        *   **成交量确认**：成交量比 (Vol Ratio) 放大，显示机构进场迹象。
    3.  **Fallback 兜底**：若环境优良但无明确信号，强制选取 20 日表现最强的 Top 3 标的进入下一轮，防止错过“静默启动”的牛股。

### 层级 3：多智能体深度投研 (Phase 3 - Expert Matrix)
*   **触发时间**：通过层级 2 筛选后的每一只候选股。
*   **动作**：
    *   **FundamentalAnalyst**：计算 PEG、毛利率及机构动向。
    *   **TechnicalAnalyst**：识别杯柄形态、计算精确支撑阻力。
    *   **SentimentAnalyst**：扫描 Reddit/Twitter 情绪及最新利空消息。
*   **终审**：将所有专家报告汇总为 `Decision Briefing` 提交给 **CIO (ReAct Agent)** 进行最终审判。

---

## 架构设计 (Strategic Multi-Agent)

```
┌─────────────────────────────────────────────────────────────┐
│                    Main Loop (V3.0 Engine)                  │
├─────────────────────────────────────────────────────────────┤
│ Phase 1: Data Gathering (Account, Market, Scan)             │
├─────────────────────────────────────────────────────────────┤
│ Phase 2: Macro Risk Scoring (0-100 Score)                   │
├───────────────────────────────┬─────────────────────────────┤
│ Phase 3: EXPERT ORCHESTRATION │ (Parallel Execution)        │
│ ┌──────────────────────────┐  │ ┌────────────────────────┐  │
│ │ Fundamental Analyst      │  │ │ Technical Analyst      │  │
│ │ (PE, PEG, Revenue, SWOT) │  │ │ (Stage 2, RS, MACD...) │  │
│ └─────────────┬────────────┘  │ └────────────┬───────────┘  │
│               └───────┬───────┴──────────────┘              │
│                       ▼                                     │
│            ┌──────────────────────┐                         │
│            │  Sentiment Analyst   │                         │
│            │ (News, FOMO, Reddit) │                         │
│            └──────────┬───────────┘                         │
├───────────────────────▼─────────────────────────────────────┤
│ Phase 4: CIO STRATEGIC DECISION (ReAct Loop)                │
│ -> Analyze Decision Briefings                               │
│ -> Resolve Identified Conflicts                             │
│ -> Execute Precision Trading                                │
├─────────────────────────────────────────────────────────────┤
│ Phase 5: Daily Post-Market Review & Memory Compression      │
└─────────────────────────────────────────────────────────────┘
```

---

## 专家团详情

### 1. 基本面专家 (FundamentalAnalyst)
- **职责**：挖掘公司核心护城河、估值水平及业绩指引。
- **核心指标**：PE (TTM), Forward PE, PEG (核心准则), 毛利率趋势, 机构持仓变动。

### 2. 技术面专家 (TechnicalAnalyst)
- **职责**：基于 Mark Minervini 的趋势模板进行形态识别。
- **核心指标**：Stage 2 确认, 相对强度 (RS vs SPY), SMA 20/50/200 排列, ADX 趋势强度。

### 3. 舆情专家 (SentimentAnalyst)
- **职责**：捕捉市场情绪过热或过度恐慌的信号。
- **核心来源**：X (Twitter), Reddit (WSB), 金融新闻网站。

---

## 执行流程

### CIO 的决策艺术
在 v3.0 中，主 Agent 的推理逻辑遵循 **"审判"模式**：

1.  **首查矛盾**：如果基本面显示 `Undervalued` 但技术面显示 `Stage 4`，CIO 会立即启动额外搜索，调查是否有未公开的利空。
2.  **硬约束过滤**：即便所有专家都看看好，只要技术面不符合 `Stage 2` 或股价在 `SMA50` 之下，买入指令将被否决。
3.  **动态仓位**：根据 `Macro Risk Score` 和专家共鸣程度，自动计算 40% (试探), 70% (标准) 或 100% (满额) 的计划仓位。

---

## 快速开始

### 环境变量更新 (v3.0 推荐)
建议在 `.env` 中配置分析师专用模型：

```bash
# ── 主 LLM (用于决策) ──
LLM_PROVIDER=gemini
GEMINI_API_KEY="your_pro_key"
GEMINI_MODEL="gemini-3.1-pro-preview"

# ── 分析师专用 LLM (用于并行扫描) ──
ANALYST_LLM_PROVIDER=gemini
ANALYST_GEMINI_API_KEY="your_flash_key"
ANALYST_GEMINI_MODEL="gemini-3-flash-preview"
```

---

## 免责声明
本项目仅供学习和研究目的。实盘交易风险巨大，请务必在充分了解风险并有专人监控的情况下运行。
