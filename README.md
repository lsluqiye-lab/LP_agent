# LP-Agent v3.0

基于 **Strategic Multi-Agent** 架构的美股自动交易智能体。系统模拟对冲基金运行模式，由 **CIO (首席投资官)** 决策大脑统筹多个**领域专家智能体**，通过 LongPort OpenAPI 执行实盘交易。

系统核心哲学：**Bear-Case-First (风险优先)** —— 所有的买入必须建立在对风险因素的彻底调查和证伪之上。

---

## 项目概述

LP-Agent v3.0 标志着从“自动化脚本”向“智能化投研系统”的本质跃迁。它不再仅仅是机械地执行规则，而是具备了跨维度思考、识别信息矛盾、以及深度追问的能力。

### V3.0 核心突破

- **专家协作矩阵 (Expert Matrix)**：引入了三个独立的领域专家，分别负责基本面、技术面和舆情监控，提供深度结构化简报。
- **CIO 决策中枢**：主 ReAct Agent 升级为 CIO 角色，专注处理专家意见冲突，通过 `identified_conflicts` 机制捕捉“好公司但技术走势差”等潜在陷阱。
- **混合模型策略 (Hybrid LLM Strategy)**：
    - **专家层**：默认使用 **Gemini-3-Flash-Preview**（2000+ RPM），实现极致的并行扫描速度和低成本。
    - **决策层**：使用 **Gemini-3.1-Pro-Preview** (或 DeepSeek-V3)，提供最高等级的逻辑推理和风险研判。
- **高并发投研链**：通过异步 IO，系统可以在 30 秒内完成对 10+ 只标的的全方位“核磁共振”式体检。

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
- **输出**：`Valuation` (Undervalued / Fair / Overvalued), `Strengths`, `Weaknesses`。

### 2. 技术面专家 (TechnicalAnalyst)
- **职责**：基于 Mark Minervini 的趋势模板进行形态识别。
- **核心指标**：Stage 2 确认, 相对强度 (RS vs SPY), SMA 20/50/200 排列, ADX 趋势强度, RSI 状态。
- **输出**：`TrendStage` (1/2/3/4), `Support/Resistance Levels`, `Key Signals`。

### 3. 舆情专家 (SentimentAnalyst)
- **职责**：捕捉市场情绪过热或过度恐慌的信号。
- **核心来源**：X (Twitter), Reddit (WSB), 金融新闻网站。
- **输出**：`MarketSentiment` (Fear/Neutral/Greed), `Key News Catalysts`。

---

## 执行流程

### CIO 的决策艺术
在 v3.0 中，主 Agent 的推理逻辑遵循 **"审判"模式**：

1.  **首查矛盾**：如果基本面显示 `Undervalued` 但技术面显示 `Stage 4`，CIO 会立即启动额外搜索，调查是否有未公开的利空或机构正在大举出货。
2.  **硬约束过滤**：即便所有专家都看好，只要技术面不符合 `Stage 2` 或股价在 `SMA50` 之下，买入指令将被否决。
3.  **动态仓位**：根据 `Macro Risk Score` 和专家共鸣程度，自动计算 40% (试探), 70% (标准) 或 100% (满额) 的计划仓位。

---

## 快速开始

### 环境变量更新 (v3.0 推荐)
为了发挥 V3.0 的最大性能，建议在 `.env` 中同时配置分析师专用模型：

```bash
# ── 主 LLM (用于决策) ──
LLM_PROVIDER=gemini
GEMINI_API_KEY="your_pro_key"
GEMINI_MODEL="gemini-3.1-pro-preview"

# ── 分析师专用 LLM (用于并行扫描) ──
ANALYST_LLM_PROVIDER=gemini
ANALYST_GEMINI_API_KEY="your_flash_key" # 可复用同一个 Key
ANALYST_GEMINI_MODEL="gemini-3-flash-preview" # 2026 最新 Flash 模型
```

### 运行
```bash
chmod +x start.sh
./start.sh
```

---

## 项目结构更新
- `agent/orchestrator.py`: **[New]** 专家团调度中枢。
- `agent/fundamental_analyst.py`: **[New]** 基本面分析师。
- `agent/technical_analyst.py`: **[New]** 技术面分析师。
- `agent/sentiment_analyst.py`: **[New]** 舆情分析师。
- `agent/schemas.py`: **[New]** 全系统结构化通信协议。
- `agent/react.py`: **[Upgrade]** 升级为 V3.0 Strategic Brain。

---

## 免责声明
本项目仅供学习和研究目的。V3.0 涉及更复杂的模型交互，交易决策可能受到模型幻觉影响。实盘交易风险巨大，请务必在充分了解风险并有专人监控的情况下运行。
