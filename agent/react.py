"""
ReAct 智能体 v2.0
实现 Reasoning + Acting 的循环推理框架

v2.0 改造:
  - Bear-case-first 推理: 先找反面证据再做决策
  - 风控评分约束: 根据 RiskManager 动态调整行为
  - 固定标的池: 仅交易 WATCHLIST 内的10只股票
  - 结构化决策输出: 完整的逻辑链条
"""
import json
import logging
import uuid
from typing import Optional

from llm.base import BaseLLM, ChatMessage, Role, LLMResponse, ToolCall
from tools.base import ToolRegistry
from config import WATCHLIST


# ═══════════════════════════════════════════
# 系统提示词 v2.0 - Bear-Case-First 推理
# ═══════════════════════════════════════════

TRADING_SYSTEM_PROMPT = """你是一个专业的美股**中长线趋势交易智能体**，管理一个固定的10只精选标的池。
你的核心哲学：**买之前先想清楚风险，但看到机会时必须果断行动。空仓也有成本。**

## 你的能力

{tools_section}

## ═══════════════════════════════════════
## 一、固定标的池（仅交易这10只股票）
## ═══════════════════════════════════════

""" + ", ".join(WATCHLIST) + """

## ═══════════════════════════════════════
## 二、宏观风控约束
## ═══════════════════════════════════════

{risk_context}

风控级别对应行为：
- LOCKDOWN (<30): 禁止买入，考虑减仓
- CAUTIOUS (30-50): 仅允许持有或减仓
- NORMAL (50-70): 正常交易，仓位按倍率执行
- FAVORABLE (>70): 环境良好，**应积极寻找建仓机会**

## ═══════════════════════════════════════
## 三、多阶段分析工作流（核心！必须严格执行）
## ═══════════════════════════════════════

**你必须按以下 4 个阶段依次执行，每个阶段都需要调用对应的工具。不允许跳过任何阶段！**
**不允许在只完成第1阶段后就直接输出最终结论。**

### 🔷 阶段 1: 技术面分析（必须执行）
对候选清单中的每只股票调用 `get_technical_analysis`，获取完整技术指标。
这是基础数据，后续阶段的分析都建立在此之上。
**重要：你可以一次性对所有候选标的并行调用 get_technical_analysis，不需要一个一个调用。**

### 🔷 阶段 2: 基本面 + 资金面分析（必须执行）
在获得技术面数据后，**你必须继续调用以下工具**：
- `get_fundamentals`: 获取候选标的的 PE/PB/市值/多周期涨跌幅等估值数据
- `get_capital_flow`: 对技术面信号较强的标的（stage2=true 或 signal_summary.score >= 2），获取主力资金流向（大/中/小单分布），判断主力态度

**不要在只有技术面数据的情况下就做出买入决策。** 基本面和资金面能帮助你验证技术信号的可靠性。
**重要：你可以一次性并行调用 get_fundamentals 和多个 get_capital_flow，不需要串行等待。**

### 🔷 阶段 3: 消息面 + 舆情分析（必须执行）
**你必须调用搜索工具获取市场信息，不能仅凭数字做决策：**
- `search_stock_news`: 对候选标的搜索最新新闻，了解是否有财报、并购、产品发布等重大事件
- `search_financial_analysis`: 搜索分析师评级和目标价，了解机构观点
- `search_market_sentiment`: 对有强信号的标的搜索社交媒体舆情，了解散户情绪

如果某个搜索工具调用失败或超时，**记录失败原因后继续执行后续工具和阶段，不因搜索失败而停止整个流程**。
**重要：你可以一次性并行调用多个搜索工具（如同时搜索多只股票的新闻和分析师评级），大幅提升效率。**

### 🔷 阶段 4: 综合研判 + 最终决策
**在完成以上 3 个阶段的数据收集后**，才能综合所有维度做出最终决策。
对每只候选标的，按 Bear-Case-First 框架完成分析：

**Step 1 — Bear Case（风险因素）**：
汇总技术面负面信号 + 基本面风险（估值过高、资金外流等）+ 消息面利空

**Step 2 — Bull Case（正面因素）**：
汇总技术面正面信号 + 基本面支撑（估值合理、资金流入等）+ 消息面利好 + 分析师评级

**Step 3 — 做出决策**：
- 存在"致命级"风险（跌破SMA200、RSI<30、重大利空新闻） → SELL/HOLD
- 风险因素可控 + 正面因素占优 + 无重大消息面利空 → 可以 BUY
- 风险与正面因素均衡 → HOLD，但标注关注价位

**重要：没有发现明确风险 ≠ 风险很大，而是说明技术面相对干净，可以推进到买入条件检查。**

## ═══════════════════════════════════════
## 四、买入条件（分级制，非全部AND）
## ═══════════════════════════════════════

### 必要条件（缺一不可）:
1. Stage 2 上升趋势: 股价 > SMA50 > SMA200
2. 风控评分允许买入（NORMAL 或 FAVORABLE）
3. 周线 RSI(14) 在 35~80 之间（非极端区域）

### 加分条件（满足越多越好，≥3项即可建仓）:
- ADX > 25 且方向看多（趋势有强度） +1
- MACD 柱状图为正 或 近期金叉 +1
- 布林带 %B > 0.3 +1
- OBV 无看空背离 +1
- 近期成交量 ≥ 1.2 × 50日均量 +1
- 20日收益跑赢 SPY +1
- signal_summary.score ≥ 3 +1
- 主力资金净流入（get_capital_flow 显示 large_net > 0） +1
- 分析师评级以买入/增持为主（search_financial_analysis） +1

### 建仓规模与加分条件挂钩:
- 3项加分 → 试探性建仓（计划仓位的 40%）
- 4-5项加分 → 标准建仓（计划仓位的 70%）
- 6项以上 → 满额建仓（计划仓位的 100%）

### 仓位计算:
```
预期止损% = max(8%, 2 × ATR%)
计划仓位金额 = min(可用现金 × 单笔上限% × 风控倍率, 总资产 × 1.5% / 预期止损%)
实际金额 = 计划仓位金额 × 建仓规模比例（40%/70%/100%）
交易数量 = floor(实际金额 / 当前股价)
```

## ═══════════════════════════════════════
## 五、卖出决策框架
## ═══════════════════════════════════════

### 止损卖出（硬性规则）
1. 股价从买入价下跌 8%~12% → 立即止损
2. 股价有效跌破 SMA50（连续2-3日收盘在下方）
3. 单笔亏损 > 总资产的 1.5% → 强制止损
4. ATR 止损: 跌破 买入价 - 2×ATR

### 移动止盈
1. 盈利 > 20% 后，止盈线上移至 SMA20
2. 盈利 > 50% 后，止盈线上移至 SMA50
3. 跌破止盈线 → 卖出

## ═══════════════════════════════════════
## 六、行动候选清单（系统注入，必须逐一处理）
## ═══════════════════════════════════════

{action_candidates}

**你必须对上面每只候选股票严格执行阶段1→2→3→4的完整分析流程。**
**不允许跳过任何一只候选股票。不允许在只调用了 get_technical_analysis 后就直接给出最终决策。**

## ═══════════════════════════════════════
## 八、工具调用效率要求（重要！）
## ═══════════════════════════════════════

**你必须尽可能在一次响应中并行调用多个工具，而不是每次只调用一个工具。**

示例：
- ✗ 错误：第1轮调用 get_technical_analysis(AAPL)，第2轮调用 get_technical_analysis(NVDA)...
- ✓ 正确：第1轮同时调用 get_technical_analysis(AAPL)、get_technical_analysis(NVDA)、get_technical_analysis(TSLA)...

- ✗ 错误：第1轮调用 get_fundamentals，第2轮调用 get_capital_flow，第3轮调用 search_stock_news...
- ✓ 正确：第1轮同时调用 get_fundamentals、get_capital_flow(AAPL)、get_capital_flow(NVDA)、search_stock_news(AAPL)...

**并行调用可以显著减少迭代次数，提升分析效率。同一阶段内的工具调用、以及不同阶段间无依赖的工具调用，都应该并行执行。**

## ═══════════════════════════════════════
## 七、输出格式（阶段4最终输出时使用）
## ═══════════════════════════════════════

```
【风控状态】评分 XX/100 (级别) | 允许买入: 是/否

【持仓巡检】(如有持仓)
  XXXX: $价格 | 盈亏 +X% | 止损线 $XX → HOLD/SELL + 理由

【候选分析】(对每只候选标的)
  XXXX:
    技术面: Stage2=✓/✗ | RSI=XX | MACD=金叉/死叉/正/负 | ADX=XX
    基本面: PE=XX | PB=XX | 市值=XXX | 近期涨跌=XX%
    资金面: 主力净流入/流出 | 大单方向
    消息面: 关键新闻摘要 | 分析师评级
    Bear Case: 综合技术+基本面+消息面的风险因素
    Bull Case: 综合技术+基本面+消息面的正面因素
    加分项: X/9
    → 决策: BUY XX股 @ $XXX (试探/标准/满额) / HOLD 关注$XXX突破

【下轮关注】需监控的关键价位或事件
```
"""


class ReActAgent:
    """
    ReAct智能体 v2.0

    改造:
    - 支持动态注入风控上下文
    - pre_run_tools 结果同时作为 RiskManager 输入
    - 交易通知通过飞书推送
    """

    def __init__(
        self,
        llm: BaseLLM,
        tool_registry: ToolRegistry,
        system_prompt: str = TRADING_SYSTEM_PROMPT,
        max_iterations: int = 10,
        pre_run_tools: Optional[list[str]] = None,
        feishu_notifier=None,
        logger: Optional[logging.Logger] = None
    ):
        self.llm = llm
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.pre_run_tools = pre_run_tools or []
        self.feishu_notifier = feishu_notifier
        self.logger = logger or logging.getLogger(__name__)

    def _check_and_notify_trading(self, result: str, executed_tools: list[str]) -> None:
        """检查是否执行了交易操作，有则发飞书通知"""
        if not self.feishu_notifier:
            return
        trading_tools = {"buy_stock", "sell_stock"}
        actual_trading = [t for t in executed_tools if t in trading_tools]
        if actual_trading:
            self.logger.info(f"检测到交易操作: {actual_trading}，发送飞书通知")
            self.feishu_notifier.send_trading_alert(result)

    def _build_tools_section(self, available_tools: list[dict]) -> str:
        """动态生成工具说明"""
        market_lines = []
        trade_lines = []
        search_lines = []

        for t in available_tools:
            name = t["function"]["name"]
            tool = self.tool_registry.get(name)
            desc = tool.description if tool else t["function"].get("description", "")

            if name.startswith("search_"):
                search_lines.append(f"- {name}: {desc}")
            elif name.startswith("get_") or name.startswith("scan_"):
                market_lines.append(f"- {name}: {desc}")
            else:
                trade_lines.append(f"- {name}: {desc}")

        parts = []
        if market_lines:
            parts.append("### 行情数据工具\n" + "\n".join(market_lines))
        if trade_lines:
            parts.append("### 交易执行工具\n" + "\n".join(trade_lines))
        if search_lines:
            parts.append("### 信息搜索工具\n" + "\n".join(search_lines))
        return "\n\n".join(parts) if parts else "（暂无可用工具）"

    def run(
        self,
        user_input: Optional[str] = None,
        risk_context: str = "风控状态: 未计算",
        pre_executed_data: Optional[dict] = None,
        action_candidates: str = "无候选标的（标的池扫描未产生信号）",
    ) -> str:
        """
        运行智能体

        Args:
            user_input: 用户输入
            risk_context: 风控摘要文本（由 main.py 注入）
            pre_executed_data: 预执行的数据（由 main.py 收集）
            action_candidates: 行动候选清单文本（由 main.py 从 scan 结果中提取）

        Returns:
            智能体的最终响应
        """
        # 过滤掉已预执行的工具
        pre_run_set = set(self.pre_run_tools)
        tools = [
            t for t in self.tool_registry.get_openai_tools()
            if t["function"]["name"] not in pre_run_set
        ]

        # 渲染系统提示词
        system_prompt = self.system_prompt.replace(
            "{tools_section}", self._build_tools_section(tools)
        ).replace(
            "{risk_context}", risk_context
        ).replace(
            "{action_candidates}", action_candidates
        )

        # 初始化消息
        messages = [
            ChatMessage(role=Role.SYSTEM, content=system_prompt)
        ]

        if user_input:
            messages.append(ChatMessage(role=Role.USER, content=user_input))
        else:
            messages.append(ChatMessage(
                role=Role.USER,
                content=(
                    "执行本轮完整的多阶段分析，你必须严格按 4 个阶段依次推进，不允许跳过任何阶段：\n\n"
                    "【阶段1 — 技术面】对所有候选标的并行调用 get_technical_analysis，一次性获取全部技术指标。\n"
                    "【阶段2 — 基本面+资金面】并行调用 get_fundamentals 和多个 get_capital_flow，一次性获取估值和资金数据。\n"
                    "【阶段3 — 消息面+舆情】并行调用 search_stock_news、search_financial_analysis 等，一次性获取新闻和分析师评级。\n"
                    "【阶段4 — 综合决策】汇总所有维度数据，对每只标的完成 Bear/Bull/Decision 分析，输出最终报告。\n\n"
                    "同时巡检现有持仓是否触发止损/止盈条件。\n"
                    "**重要：每个阶段你必须一次性并行调用所有需要的工具，不要一个一个串行调用！**\n"
                    "请从阶段1开始，对所有候选标的并行调用 get_technical_analysis。"
                ),
            ))

        # 注入预执行数据
        if pre_executed_data:
            context_parts = []
            for key, value in pre_executed_data.items():
                if isinstance(value, str):
                    context_parts.append(f"[{key}]\n{value}")
                else:
                    context_parts.append(f"[{key}]\n{json.dumps(value, ensure_ascii=False)}")

            if context_parts:
                context_msg = "以下是已收集的背景数据（由系统自动执行）：\n\n" + "\n\n".join(context_parts)
                messages.append(ChatMessage(role=Role.USER, content=context_msg))

        # 传统 pre_run_tools（兼容老逻辑）
        elif self.pre_run_tools:
            self.logger.info(f"预执行工具: {self.pre_run_tools}")
            pre_results = []
            for tool_name in self.pre_run_tools:
                if self.tool_registry.get(tool_name) is None:
                    self.logger.warning(f"预执行工具 '{tool_name}' 未注册，跳过")
                    continue
                self.logger.info(f"预执行: {tool_name}")
                try:
                    result = self.tool_registry.execute(tool_name)
                    self.logger.info(f"预执行结果: {result}")
                    pre_results.append(f"[{tool_name}]\n{result}")
                except Exception as e:
                    error_msg = json.dumps({"error": str(e)})
                    self.logger.error(f"预执行工具 '{tool_name}' 出错: {e}")
                    pre_results.append(f"[{tool_name}]\n{error_msg}")

            if pre_results:
                context_msg = "以下是已获取的背景信息（由系统自动收集）：\n\n" + "\n\n".join(pre_results)
                messages.append(ChatMessage(role=Role.USER, content=context_msg))

        # 记录执行过的工具
        executed_tools: list[str] = []

        # ReAct 循环
        self.logger.info("=" * 50)
        self.logger.info("开始 ReAct 循环 (Bear-Case-First)")

        for iteration in range(self.max_iterations):
            self.logger.info(f"--- 迭代 {iteration + 1}/{self.max_iterations} ---")

            try:
                response = self.llm.chat(messages, tools=tools, tool_choice="auto")

                if not response.has_tool_calls:
                    self.logger.info("LLM完成任务")
                    self.logger.info(f"最终响应: {response.content}")
                    final_result = response.content or "任务完成"
                    self._check_and_notify_trading(final_result, executed_tools)
                    return final_result

                assistant_message = ChatMessage(
                    role=Role.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                    native_content=response.native_content,
                )
                messages.append(assistant_message)

                for tool_call in response.tool_calls:
                    self.logger.info(f"调用工具: {tool_call.name}")
                    self.logger.info(f"参数: {json.dumps(tool_call.arguments, ensure_ascii=False)}")
                    executed_tools.append(tool_call.name)

                    try:
                        result = self.tool_registry.execute(
                            tool_call.name, **tool_call.arguments
                        )
                        self.logger.info(f"工具返回: {result}")
                    except Exception as e:
                        result = json.dumps({"error": str(e)})
                        self.logger.error(f"工具执行错误: {e}")

                    tool_message = ChatMessage(
                        role=Role.TOOL,
                        content=result,
                        tool_call_id=tool_call.id,
                        name=tool_call.name
                    )
                    messages.append(tool_message)

            except Exception as e:
                self.logger.error(f"ReAct循环出错: {e}")
                return f"执行出错: {str(e)}"

        # 达到最大迭代
        self.logger.warning(f"达到最大迭代次数 {self.max_iterations}")
        messages.append(ChatMessage(
            role=Role.USER,
            content="已达最大步数，请总结当前 Bear-Case-First 分析结果并给出最终建议。"
        ))

        try:
            final_response = self.llm.chat(messages, tools=None)
            final_result = final_response.content or "达到最大迭代次数"
            self._check_and_notify_trading(final_result, executed_tools)
            return final_result
        except Exception as e:
            return f"最终总结出错: {str(e)}"

    def run_once(self, user_input: str) -> str:
        """单次运行（不循环）"""
        messages = [
            ChatMessage(role=Role.SYSTEM, content=self.system_prompt),
            ChatMessage(role=Role.USER, content=user_input)
        ]
        tools = self.tool_registry.get_openai_tools()

        try:
            response = self.llm.chat(messages, tools=tools)
            if response.has_tool_calls:
                results = []
                for tool_call in response.tool_calls:
                    try:
                        result = self.tool_registry.execute(
                            tool_call.name, **tool_call.arguments
                        )
                        results.append(f"{tool_call.name}: {result}")
                    except Exception as e:
                        results.append(f"{tool_call.name}: Error - {str(e)}")
                return "\n".join(results)
            else:
                return response.content or "无响应"
        except Exception as e:
            return f"执行出错: {str(e)}"
