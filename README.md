# LP-Agent v3.0

基于 **Strategic Multi-Agent** 架构的美股自动交易智能体。系统模拟华尔街对冲基金运行模式，由 **CIO (首席投资官)** 决策大脑统筹多个**领域专家智能体**，并结合高频 Watchdog，通过 LongPort OpenAPI 执行实盘狙击。

系统核心哲学：**Bear-Case-First (风险优先) & Tactical Execution (战术狙击)** —— 宁可错过，绝不追高被套。

---

## 🚀 LP-Agent v3.0 完整生命周期 (以 TSLA 为例)

系统不再是机械地定时扫盘，而是具备“嗅觉”、“肌肉记忆”和“狙击能力”的智能体。以下是系统在一天中如何捕获并交易 TSLA 的完整流程：

```mermaid
sequenceDiagram
    participant Time as 盘前/盘中时段
    participant Phase0 as Alpha Scanner (选股)
    participant WD as 高频 Watchdog (风控)
    participant Expert as 专家矩阵 (分析)
    participant CIO as 主脑 CIO (决策)
    participant Broker as LongPort (执行)

    Note over Time, Broker: 🌅 美东时间 09:00 (盘前)
    Time->>Phase0: 唤醒盘前雷达
    Phase0->>Phase0: 搜索全网新闻 "US top growth stocks breakout"
    Phase0-->>CIO: 发现 TSLA 存在“A15芯片流片”催化剂，将其加入今日 Watchlist

    Note over Time, Broker: ⏰ 10:00 (早盘决策期)
    Time->>Expert: 触发多专家并发研报
    Expert->>Expert: Fundamental: 发现估值极高，但 FSD 进展迅速<br/>Technical: 量价齐升 (OBV看多背离)，算出阻力位 $398.01<br/>Sentiment: 市场情绪极度贪婪 (FOMO)
    Expert-->>CIO: 提交综合决策简报 (Decision Briefing)

    CIO->>CIO: 检查交易记忆：近14天无被套记录<br/>宏观评分：65分 (安全)<br/>技术面：处于阻力位下方，未突破
    CIO->>Broker: 下达【LIT 触及限价单】，触发价设在阻力位上方($400)<br/>坚守右侧交易：“不见兔子不撒鹰”

    Note over Time, Broker: ⚡ 盘中随机时间 (e.g. 13:15)
    Time->>WD: 每分钟/15分钟心跳
    WD->>WD: 1. 价格跌破成本8%？否<br/>2. 检索全网是否有核弹级突发新闻？否

    Note over Time, Broker: 🔔 15:30 (尾盘决策期)
    Time->>CIO: 再次唤醒
    CIO->>CIO: 发现持仓中有走弱的股票 (WEAK_POSITION)<br/>且 TSLA 依然极强 (STRONG_SIGNAL)
    CIO->>Broker: 执行“配对换仓” (Pair Trading)，卖弱买强
    
    Note over Time, Broker: 🌙 16:30 (收盘后)
    Time->>CIO: 触发 Review Agent 每日复盘
    CIO->>CIO: 总结今日盈亏，提取 1-3 条交易教训写入长效记忆
```

---

## 🧠 系统核心能力升级

### 1. 动态雷达：Phase 0 (Alpha Scanner)
系统告别了死板的硬编码标的池。每天盘前 (09:00)，Alpha Scanner 会自动在全网检索最近一周的强势板块、机构评级上调以及具有爆发催化剂的股票，自动将 10-15 只“金股”热更新进当日的内存池。今天的主线是 AI，明天可能就会自动切换到核电或生物医药。

### 2. 战术狙击手：告别无脑市价单 (LIT & LO 订单)
在震荡市中，市价单 (MO) 是被割韭菜的罪魁祸首。
- **技术点位绑定**：技术面专家被强制要求精确计算支撑位 (Support) 和阻力位 (Resistance)。
- **LIT 突破单**：对于看好的未突破股票，CIO 被强制使用 **LIT (触及限价单)**，将买单挂在阻力位上方 0.5% 处，只买确定的突破。
- **LO 低吸单**：对于强趋势的回调，CIO 会在均线支撑位挂 **LO (限价单)** 埋伏。

### 3. 三重防线：极速与深度的完美结合
1. **秒级硬止损 (Watchdog)**：每 1 分钟纯本地扫描一次持仓，一旦跌破成本价 8%，直接无脑市价斩仓，绝不交给大模型思考。
2. **盘中防空警报 (News Watchdog)**：每 15 分钟扫描一次带血腥味的突发宏观新闻（如战争、暴雷）。一旦发现，强行拉响警报唤醒 CIO 紧急避险。
3. **宏观评分硬拦截**：当大盘技术面破位或市场过热导致宏观评分跌破 50 时，交易执行层会在物理层面没收 CIO 的“买入按钮”，仅允许卖出。

### 4. 汰弱留强与肌肉记忆
- **配对交易 (Pair Trading)**：CIO 能够识别组合内的极弱标的 (`WEAK_POSITION`) 和极强候选 (`STRONG_SIGNAL`)，自动卖出弱势股去换仓强势股。
- **长效记忆**：系统在组装研报时会附带过去 14 天该标的的战绩记录。如果系统发现自己在某只股票上反复亏损/频繁止损，会自动触发防御机制，避免变成绞肉机。

---

## 🛠️ 快速开始

### 环境变量要求
建议在 `.env` 中配置至少两个级别的模型，以兼顾决策深度和扫盘速度：

```bash
# ── 主脑 CIO (需具备极高逻辑推理能力) ──
LLM_PROVIDER=gemini
GEMINI_API_KEY="your_pro_key"
GEMINI_MODEL="gemini-3.1-pro-preview"

# ── 专家与巡检犬 (需快响应，低成本) ──
ANALYST_LLM_PROVIDER=gemini
ANALYST_GEMINI_API_KEY="your_flash_key"
ANALYST_GEMINI_MODEL="gemini-3-flash-preview"
```

### 启动命令
使用随附的脚本安全启动并管理进程：
```bash
./start.sh
```
实时查看系统流心与交易日志：
```bash
tail -f agent.log
```

---

## ⚠️ 免责声明
本项目仅供学习和研究目的。自动交易具备极高的资金风险，实盘接入前务必在纸面交易 (Paper Trading) 或模拟账户中长期验证。请务必在有专人监控的情况下运行。
