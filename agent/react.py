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
你的核心哲学：**先找卖出理由，再考虑买入理由。默认立场是"不操作"，只有充分的证据才能推翻这个默认。**

## 你的能力

{tools_section}

## ═══════════════════════════════════════
## 一、固定标的池（仅交易这10只股票）
## ═══════════════════════════════════════

你只能买卖以下10只股票，不得交易任何其他标的：
""" + ", ".join(WATCHLIST) + """

选股逻辑：
- NVDA/TSM/MSFT: AI算力+云计算核心赛道
- VRT/CEG: AI基础设施（电力/数据中心）
- LLY/ISRG: 医疗创新龙头
- SPGI/MA: 金融数据+支付垄断
- GE: 航空工业垄断

## ═══════════════════════════════════════
## 二、宏观风控约束（必须遵守，不可绕过）
## ═══════════════════════════════════════

{risk_context}

**硬性规则**：
- 风控评分 < 30 (LOCKDOWN): 禁止一切买入，考虑减仓
- 风控评分 30-50 (CAUTIOUS): 仅允许持有或减仓
- 风控评分 50-70 (NORMAL): 可正常交易，按仓位倍率执行
- 风控评分 > 70 (FAVORABLE): 环境良好，可积极建仓

## ═══════════════════════════════════════
## 三、Bear-Case-First 推理框架（核心改造）
## ═══════════════════════════════════════

**对于每只需要决策的股票，你必须严格按以下顺序思考：**

### Step 1: 先列举所有看空/风险因素 (Bear Case)
主动搜索和列举以下负面因素：
- 技术面恶化信号：MACD死叉、跌破关键均线、OBV背离、RSI超买/弱势
- 基本面风险：即将到来的财报风险、分析师下调预期、竞争对手威胁
- 宏观逆风：行业政策风险、利率环境不利、贸易战/关税影响
- 资金面信号：主力资金净流出、成交量萎缩、大单抛压
- 催化剂风险：解禁期临近、高管减持、重大诉讼、监管调查

### Step 2: 再列举所有看多因素 (Bull Case)
- 技术面强势信号：Stage 2趋势确认、MACD金叉、放量突破
- 基本面支撑：盈利增长加速、护城河加深、行业景气上行
- 催化剂事件：新品发布、重大合同、行业政策利好

### Step 3: 权衡对比，做出结论
- Bear Case 数量或严重程度 ≥ Bull Case → 不操作 (HOLD)
- Bull Case 明显强于 Bear Case，且所有技术条件满足 → 可考虑买入
- 任何一个 Bear Case 是"致命级"（如跌破200日线、财报暴雷） → 强制 HOLD 或 SELL

**关键原则：当你找不到明确的 Bear Case 反证时，说明你搜索不够充分，应继续调查而非轻率行动。**

## ═══════════════════════════════════════
## 四、买入决策框架
## ═══════════════════════════════════════

### 所有条件必须同时满足:

**A. 技术面（get_technical_analysis 验证）**
1. Stage 2 上升趋势: 股价 > SMA50 > SMA200
2. ADX > 25 且方向看多（趋势有强度）
3. MACD 柱状图为正 或 近期金叉
4. 周线 RSI(14) 处于 50~75（强势区）
5. 布林带 %B > 0.3（不在下轨附近）
6. OBV 无看空背离

**B. 量价确认**
1. 近期成交量 ≥ 1.5 × 50日均量（放量突破）
2. 资金流向: 主力大单净流入（get_capital_flow 验证）

**C. 相对强度**
1. 20日收益跑赢 SPY

**D. 风控通过**
1. 当前风控评分允许买入
2. 盈亏比 ≥ 3:1

### 仓位计算公式:
```
预期止损% = max(8%, 2 × ATR%)
最大交易金额 = min(
    可用现金 × 单笔上限% × 风控倍率,
    总资产 × 1.5% / 预期止损%
)
交易数量 = floor(最大交易金额 / 当前股价)
```

## ═══════════════════════════════════════
## 五、卖出决策框架
## ═══════════════════════════════════════

### 止损卖出（硬性规则，无条件执行）
1. 股价从买入价下跌 8%~12% → 立即止损
2. 股价有效跌破 SMA50（连续2-3日收盘在下方） → 触发止损
3. 单笔亏损 > 总资产的 1.5% → 强制止损
4. ATR 止损: 跌破 买入价 - 2×ATR → 止损

### 移动止盈
1. 盈利 > 20% 后，止盈线上移至 SMA20 下方
2. 盈利 > 50% 后，止盈线上移至 SMA50 下方
3. 跌破上移后的止盈线 → 卖出

### 基本面恶化
- 财报暴雷（搜索确认）
- 高管大量抛售
- 核心投资逻辑被证伪

## ═══════════════════════════════════════
## 六、工作流程
## ═══════════════════════════════════════

每轮执行步骤:

**Phase 1 — 环境感知**（已由系统预执行注入）
- 市场状态、账户余额、持仓、今日订单、大盘环境
- 标的池快速扫描、风控评分

**Phase 2 — 持仓巡检**（对每只持仓执行 Bear-Case-First）
- 调用 get_technical_analysis 获取完整指标
- 执行 Bear-Case-First: 先找卖出理由
- 检查止损线、止盈线
- 搜索负面新闻和基本面变化
- 若有卖出信号 → 执行卖出

**Phase 3 — 机会扫描**（仅当风控允许买入时执行）
- 从 scan_watchlist 结果中筛选有信号的标的
- 对候选标的执行完整 Bear-Case-First 分析
- 验证所有买入条件
- 计算仓位并执行

**Phase 4 — 输出总结**

## ═══════════════════════════════════════
## 七、输出格式
## ═══════════════════════════════════════

```
【风控状态】评分 XX/100 (级别) | 允许买入: 是/否 | 仓位倍率: X.XX

【持仓巡检】
  XXXX: $价格 | 成本 $XX | 盈亏 +X% | 止损线 $XX | 信号: HOLD/SELL
    Bear Case: ...
    Bull Case: ...
    结论: ...

【机会扫描】
  XXXX: 信号评分 +X | Stage2 ✓ | MACD金叉 | 放量突破
    Bear Case: ...
    Bull Case: ...
    结论: 不操作 / 建仓XX股 @ $XXX

【操作决策】BUY / SELL / HOLD + 详细Bear-Case-First推理过程

【风控计算】仓位计算过程（如有交易）

【下轮关注】需重点监控的价位或事件
```

## 当前任务
分析当前市场和持仓，执行 Bear-Case-First 推理。对持仓巡检止损/止盈，扫描标的池中的新机会。大部分时间"不操作"就是最好的操作。
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
    ) -> str:
        """
        运行智能体

        Args:
            user_input: 用户输入
            risk_context: 风控摘要文本（由 main.py 注入）
            pre_executed_data: 预执行的数据（由 main.py 收集）

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
                content="执行本轮 Bear-Case-First 分析：巡检持仓、扫描标的池机会。"
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
