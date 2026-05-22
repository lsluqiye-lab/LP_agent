# LP-Agent v3.5: 证券交易自主智能体 (Dual-Track Core)

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Status](https://img.shields.io/badge/Status-Trading-success.svg)](#)

LP-Agent v3.5 是一款基于 **Strategic Multi-Agent (SMA)** 架构的美股量化交易智能体。系统模拟专业对冲基金运行模式，由 **CIO (首席投资官)** 统筹技术、基本面、舆情、量化、板块轮动五大专家矩阵，并结合 **Watchdog (毫秒生存级监控)** 与 **Long-term Memory (自主进化记忆)**，实现从盘前选股、本地硬规则初筛、门限唤醒到战略决策下单的全闭环自动化。

---

## 🏛️ 系统逻辑架构 (Core Architecture)

*(注：以下为实时渲染的系统逻辑架构，展示了 v3.5 双轨制引擎中本地多因子计算、行业相对强度 RS 过滤、本地规则初筛 Pre-Screening 门限唤醒、以及基本面本地 Cache 机制的相互协同逻辑)*

```mermaid
graph TD
    classDef default fill:#1E293B,stroke:#475569,stroke-width:1px,color:#F8FAFC;
    classDef core fill:#0F172A,stroke:#38BDF8,stroke-width:2px,color:#F0F9FF;
    classDef qlib fill:#1e3a8a,stroke:#60a5fa,stroke-width:3px,color:#eff6ff,stroke-dasharray: 5 5;
    classDef wd fill:#7f1d1d,stroke:#f87171,stroke-width:2px,color:#fef2f2;
    classDef memory fill:#14532d,stroke:#4ade80,stroke-width:2px,color:#f0fdf4;
    classDef pre fill:#422006,stroke:#f59e0b,stroke-width:2px,color:#fef3c7;

    subgraph Dual_Track_Engine ["Dual-Track Architecture (双轨制引擎)"]
        direction TB

        %% High-Frequency Watchdog
        subgraph Watchdog ["高频监控层 (WebSocket毫秒监听 + 1分钟心跳) - 纯本地/低延迟"]
            WD_Timer((定时/推送触发))
            WD_StopLoss[ATR 动态宽容防守<br>跌破成本 2.0*ATR 止损]
            WD_TakeProfit[ATR 动态锁润机制<br>盈利 3.0*ATR 且 RSI超买]
            WD_WS[WebSocket 瞬间拦截<br>极速断路熔断 & 异动抢跑]
            
            WD_Timer --> WD_StopLoss
            WD_Timer --> WD_TakeProfit
            WD_Timer --> WD_WS
        end

        %% Strategic Brain
        subgraph Brain ["深度决策层 (定时10:00/15:30触发) - 数据与 LLM 协同驱动"]
            direction TB
            
            %% Phase 1-2.5
            subgraph Phase_Front ["数据流与前置风控 (Phase 1-2.5)"]
                MacroRisk[MacroRiskManager<br>纯本地计算大盘评分]
                PortManager[PortfolioManager<br>自适应买入阈值与杂草清理]
                AlphaScan[Alpha Scanner v3.5<br>Top-Down 自上而下动态选股]
            end

            %% Quant Factor Engine v3.5
            QuantEngine_35((("多因子行情引擎 v3.5<br>(Sector RS & 真实技术因子计算)"))):::qlib
            
            %% Phase 2.6: Local Pre-Screening
            subgraph PreScreenBlock ["本地规则初筛门限 (Phase 2.6)"]
                PreScreen[Local Pre-Screening<br>硬规则判断异动与突破]:::pre
                AutoHold[Auto-HOLD 观望<br>0 Token 挂机休眠]:::wd
            end

            %% Phase 3
            subgraph Phase_Experts ["专家并行研报 (Phase 3 - 门限激活)"]
                TechExpert[技术面专家<br>趋势确认与支撑位]
                FundExpert[基本面专家<br>PE/PEG估值与财报分析]
                FundCache[(基本面 5日 缓存<br>Fundamental Cache)]:::memory
                SentExpert[情绪舆情专家<br>Reddit/Twitter热度]
                SectExpert[板块轮动专家<br>行业 Relative Strength]
                
                FundExpert <--> FundCache
            end

            %% Phase 4
            subgraph Phase_CIO ["主脑战术决策 (Phase 4 - 门限激活)"]
                CIO[CIO Agent<br>ReAct 终极推理与资金调配]
                OrderExec[高级订单执行<br>LIT突破单/LO低吸单/配对换仓]
            end

            %% Phase 5
            subgraph Phase_Review ["复盘与记忆 (Phase 5)"]
                Review[每日复盘 Reviewer]
                Memory[(长效历史教训库<br>Trading Memory)]:::memory
            end
        end
    end

    %% Data Source
    LongPort((LongPort 交易/行情 OpenAPI))

    %% Data Flow
    LongPort -. "全量 11行业 ETF + SPY 日K线" .-> QuantEngine_35
    QuantEngine_35 ==>|"1. Sector RS 行业相对强度过滤<br>2. 50只个股100分制技术评分"| AlphaScan
    QuantEngine_35 -. "3. 注入单票量化硬指标" .-> CIO
    
    LongPort --> MacroRisk
    LongPort --> Watchdog
    LongPort --> Phase_Experts

    AlphaScan -->|"输出 15 只优选候选股"| PreScreen
    MacroRisk -->|"计算动态防守系数"| PortManager
    PortManager -->|"下发买入阈值与淘汰名单"| PreScreen
    
    PreScreen -->|"A. 活跃候选 (破位/放量突破/风控紧缩)"| Phase_Experts
    PreScreen -. "B. 无异动个股" .-> AutoHold
    
    TechExpert & FundExpert & SentExpert & SectExpert -->|"汇聚活跃研报"| CIO
    
    Memory -. "注入防坑历史教训" .-> CIO
    CIO -->|"下达战术指令"| OrderExec
    OrderExec -->|"发送实盘/模拟条件单"| LongPort
    Watchdog -->|"极速撤单 + 市价平仓单"| LongPort

    OrderExec --> Review
    Review -->|"更新每日经验"| Memory

    class Watchdog wd;
    class CIO,MacroRisk,AlphaScan,PortManager core;
```

---

## 🚀 LP-Agent v3.5 完整生命周期时序 (以 TSLA 为例)

系统不再是机械地定时盲目决策，而是融合了“本地极速硬指标打分”、“不惊扰大脑的温和休眠”以及“精准右侧出击”的智能化机器。以下是系统在一天中交易 TSLA 的完整闭环流程：

```mermaid
sequenceDiagram
    participant Time as 盘前/盘中时段
    participant Phase0 as Alpha Scanner (科学选股)
    participant Quant as 因子行情引擎 (v3.5)
    participant WD as 高频 Watchdog (防守)
    participant Pre as Local Pre-Screening (初筛)
    participant Expert as 专家矩阵 (分析)
    participant CIO as 主脑 CIO (决策)
    participant Broker as LongPort (执行)

    Note over Time, Broker: 🌅 美东时间 09:00 (盘前)
    Time->>Phase0: 唤醒盘前雷达
    Phase0->>Quant: 拉取 11 个行业 ETF 的 20 日表现
    Quant-->>Phase0: 返回 Sector RS 排名 (科技/医疗强劲)
    Phase0->>Quant: 执行 50 只 Golden Universe 明星股 100 分制多因子技术打分
    Quant-->>Phase0: 初筛出技术得分前 15 的强势股 (TSLA 评分 92)
    Phase0->>Phase0: 自动分组搜索财报日程避雷 (剔除5天内财报股)
    Phase0-->>Pre: 生成今日最科学监控标的池 (TSLA, NVDA 等) 并回填当前持仓

    Note over Time, Broker: ⏰ 10:00 (早盘决策期)
    Time->>Pre: 启动本地硬规则初筛 (Phase 2.6)
    
    alt 场景 A：持仓与监控股无任何异动 (温和平稳)
        Pre->>Pre: AAPL完美运行于均线之上，TSLA在阻力位下方横盘缩量，无买卖加减仓触发点
        Pre-->>Time: 😴 判定为 Passive (Auto-HOLD)，跳过后续所有大模型专家与 CIO 决策！本轮消耗 0 Token。
    else 场景 B：出现交易触发门限 (如 TSLA 向上放量突破 / 某持仓股跌破防守)
        Pre->>Pre: 侦测到 TSLA 股价放量拉升、量比 1.6x 突破 20 日高点，触发活跃买入信号！
        Pre->>Expert: 🎯 唤醒 Active 门限，仅对 TSLA 启动专家研报
        
        par 基本面分析 (Fundamental - 5日缓存)
            Expert->>Expert: 检查 NVDA/TSLA 本地 5日 缓存，若未过期直接命中，免去网络检索与 LLM 生成
        and 技术面分析 (Technical)
            Expert->>Quant: 精确提取支撑位 ($366) 与 突破阻力位 ($398)
        and 情绪舆情分析 (Sentiment)
            Expert->>Expert: 扫描社交情绪与突发消息催化剂
        end
        
        Expert-->>CIO: 仅生成 TSLA 的专家精炼决策简报 (节省 80% 大脑处理负荷)
        CIO->>CIO: 检查宏观评分 (NORMAL 65) 允许交易 -> TSLA 技术面右侧暴涨 -> 确定性极高
        CIO->>Broker: 撤销原未成交挂单 (Cancel-Order)，并直接执行市价单(MO)或突破买入条件单(LIT)
    end

    Note over Time, Broker: ⚡ 盘中随机时间 (e.g. 13:15)
    Time->>WD: 每分钟/15分钟心跳
    WD->>WD: 1. 价格跌破成本 2.0*ATR 安全垫？否<br/>2. 检索全网是否有未处理核弹级突发新闻？否

    Note over Time, Broker: 🌙 16:30 (收盘后)
    Time->>CIO: 触发 Review Agent 每日复盘，生成总结并写入长效记忆
```

---

## 🏛️ 智能体矩阵与能力分层 (Agent Matrix & Capability Layers)

系统将任务分为三个能力层次：**纯本地硬量化算法（Level 1）**、**轻量大语言模型专家评级（Level 2）**、**超强推理大语言模型 CIO 终裁（Level 3）**，实现性能与成本的最佳博弈。

### 0. 宏观风控局 (MacroRiskManager) - [L1]
- **职责**：盘中定时计算大盘风控分值（0-100分）。
- **打分逻辑**：综合评估 SPY技术面(25%)、市场温度(25%)、资金流向(15%)、RSI广度(15%)、市场情绪(10%)和波动率(10%)。
- **约束力**：评分 < 50 强制拦截买入，强制 CIO 只能处于防守和斩仓汰弱状态。

### 0.5 投资组合大管家 (PortfolioManager) - [L1]
- **职责**：全局仓位与平仓胜率调度。计算当前持仓的相对强度 (RS)，动态修正买入门槛（防范现金闲置或被频繁洗盘）；识别跑输大盘的“杂草标的”输出为 `weed_out_list` 供 CIO 强制斩仓。

### 1. 板块轮动与多因子量化专家 (QuantAnalyst v3.5) - [L1]
- **职责**：计算 11 个行业 ETF 的 RS 相对强度（Sector RS），并为 Golden Universe 的 50 只大中盘成长龙头计算 100 分制的技术面评分。
- **评分细则**：
  * **Trend (30分)**：价格 > SMA50 且 SMA50 > SMA200（标准 Stage 2 上行趋势）。
  * **RSI (25分)**：RSI 在 50-70 的 BULL 强势区。
  * **Relative Strength (30分)**：个股 20 日涨幅显著超越 SPY（超额 RS 比率 >= 1.05）。
  * **Volume Ratio (15分)**：20日或50日均成交量比。

### 2. 基本面专家 (FundamentalAnalyst) - [L2]
- **职责**：分析公司商业壁垒与估值红线，提供 5 日 Caching 缓存防御，避免高频调用导致的重复网络搜索与 LLM 分析。

### 3. 技术面专家 (TechnicalAnalyst) - [L2]
- **职责**：形态学专家，基于 Mark Minervini 趋势模板输出支撑位 (Support) 和突破阻力位 (Resistance) 价格。

### 4. 情绪舆情专家 (SentimentAnalyst) - [L2]
- **职责**：反向指标扫描，评估 Reddit (WSB)、Twitter 以及大盘主流媒体的情绪泡沫（Greed/Fear/FOMO）。

### 5. 首席投资官 (CIO Agent) - [L3]
- **职责**：基金决策终审脑。基于 **ReAct 终极推理框架**，仅在 Pre-Screening 门限被触发时苏醒。对矛盾专家报告（如基本面高估但技术放量突破）进行逻辑判定，选择 MO/LO/LIT 狙击手订单精准下达。

---

## 🧠 系统核心升级亮点 (V3.5 Highlighting)

### 1. 动态雷达自上而下选股 (Top-Down Alpha Scanning)
告别了死板固定的标的池或全网滞后新闻的检索。Alpha Scanner v3.5 每天盘前自动执行：
1. **行业过滤**：挑选出资金正在净流入的 Strongest Sectors（计算 11 个核心行业相对于 SPY 的 20 日表现）。
2. **个股打分**：在 50 只最具催化动能的流动性黑马中（Golden Universe），用真实 K 线在本地计算 100 分制的多因子技术得分。
3. **财报避险**：自动查询 Top 15 技术候选股未来 5 日内有无财报公布，自动剔除处于绩前财报雷区的个股（非持仓）。
4. **最终标的更新**：将精选出的 10-12 只高概率标的更新为今日 `WATCHLIST`，并**强制合并并回填当前持仓股**。

### 2. 本地硬规则初筛与门限唤醒 (Pre-Screening & Selective Activation)
为阻断每天两次深度大脑调度对 Token 的无谓浪费（90% 的巡检中个股只是处于正常波澜不惊状态）：
- **Local Pre-Screening**：对 15 只关注个股进行规则判断。
  - **持仓股 Active 门限**：跌破 20日线、触发 Watchdog 预警、利润保卫、爆量加仓异动。
  - **监控股 Active 门限**：大涨突破阻力位、成交量比 > 1.3 且 RSI 处于 50-70 上行段。
- **无异动 0 Token 挂机**：未触发任何门限时，不调用任何专家 LLM，不唤醒 CIO ReAct。系统判定 Passive (Auto-HOLD)，挂机休眠，仅通过飞书发送平稳运行简报。

### 3. 基本面 5日 缓存机制 (Fundamental Cache)
公司的竞争壁垒、估值 PEG、机构所有权变化在没有财报开盲盒的情况下是高度静态的。系统提供 `data/fundamental_cache.json` 缓存，在 5 日内对相同股票再次分析时直接读取本地缓存，**实现 0 搜索损耗、0 Token 损耗，分析速度提升 100,000 倍**！

### 4. 战术狙击手：精准高级订单机制 (LIT & LO)
- **LIT 触及限价单**：阻力位上方突破。CIO 填入纯粹支撑/阻力点位，底层交易工具通过 ATR 和当前价格区间自动调用 `_calculate_dynamic_slippage` 精准追加防御抢跑滑点，严防追高。
- **LO 低吸限价单**：均线或筹码密集区低吸回调。
- **撤单重构 (Cancel-Before-Modify)**：强制执行“先撤销再修改”原则，调用 `cancel_order` 剔除单标的同方向挂单竞争，保证订单通道干净。

---

## 🛠️ 快速开始

### 环境变量要求
建议在 `.env` 中配置至少两个级别的模型，以兼顾决策深度和扫盘速度：

```bash
# ── 主脑 CIO (需具备极高逻辑推理能力，推荐 Pro 级) ──
LLM_PROVIDER=gemini
GEMINI_API_KEY="your_pro_key"
GEMINI_MODEL="gemini-3.5-pro-preview"

# ── 专家与选股雷达 (需高响应、低成本，推荐 Flash 级) ──
ANALYST_LLM_PROVIDER=gemini
ANALYST_GEMINI_API_KEY="your_flash_key"
ANALYST_GEMINI_MODEL="gemini-3.5-flash"
```

### 启动命令
使用随附的脚本安全启动并管理进程（支持自动检查 PID 并清理旧进程）：
```bash
./start.sh
```
实时查看系统流水、因子计算与交易日志：
```bash
tail -f agent.log
```

---

## ⚠️ 免责声明
本项目仅供学习和研究目的。自动交易具备极高的资金风险，实盘接入前务必在纸面交易 (Paper Trading) 或模拟账户中长期验证。请务必在有专人监控的情况下运行。
