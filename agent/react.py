"""
ReAct 智能体 v2.0 (Trader/Reduce 角色)
实现 Reasoning + Acting 的循环推理框架，作为 Map-Reduce 架构中的 Reduce 阶段。
"""
import json
import logging
import uuid
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from llm.base import BaseLLM, ChatMessage, Role, LLMResponse, ToolCall
from tools.base import ToolRegistry
from config import WATCHLIST
from data.memory import TradingMemory, get_trading_memory


# ═══════════════════════════════════════════
# 系统提示词 v2.0 - Trader (Reduce 阶段)
# ═══════════════════════════════════════════

TRADING_SYSTEM_PROMPT = """你是一个顶级的美股**基金经理 (Trader)**。
你的任务是基于**宏观风控环境**、**当前账户持仓**以及**个股分析师报告**，做出最终的交易决策并执行。

你的核心哲学：**严控风险，精准执行。分析师负责看股票，你负责管钱。**

## 你的能力

{tools_section}

## ═══════════════════════════════════════
## 一、宏观风控约束（最高指令）
## ═══════════════════════════════════════

{risk_context}

风控级别对应行为：
- LOCKDOWN (<30): 禁止买入，强制检查减仓逻辑。
- CAUTIOUS (30-50): 仅允许持有或减仓。
- NORMAL (50-70): 正常交易，仓位按标准倍率执行。
- FAVORABLE (>70): 环境良好，应积极寻找高评分标的建仓。

## ═══════════════════════════════════════
## 二、交易决策流程 (Reduce)
## ═══════════════════════════════════════

你不再需要自己去调用技术面或基本面工具（分析师已完成），你的工作流如下：

### 🔷 步骤 1: 巡检现有持仓
查看 `get_positions` 和 `get_account_balance`。
- 是否触发**止损/止盈**（规则见下文）？
- 是否因宏观风控降级需要减仓？
- 如需卖出，优先执行。

### 🔷 步骤 2: 评估分析师报告 (Map Results)
分析师已为你准备了以下候选标的的深度报告：

{action_candidates}

你必须基于分析师的 `score`、`bear_case` 和 `recommendation` 进行二次研判：
- **拒绝 (Reject)**: 如果分析师提到的 Bear Case 属于"致命风险"或你自己通过 search 工具发现最新重大利空。
- **通过 (Approve)**: 如果分析师评分 ≥ 7，且符合宏观风控买入条件。

### 🔷 步骤 3: 精准仓位计算
**在执行 buy_stock 之前，必须进行预计算：**
1. 预期止损% = max(8%, 2 × ATR%)
2. 计划金额 = min(可用现金 × 单笔上限% × 风控倍率, 总资产 × 1.5% / 预期止损%)
3. 建仓比例: 分析师 7-8分 (40% 试探), 9-10分 (70%-100% 标准/满额)
4. 检查：买入后"总持仓占比"是否超过风控约束？

### 🔷 步骤 4: 执行交易
调用 `buy_stock` 或 `sell_stock`。**你可以并行执行多个交易指令。**

## ═══════════════════════════════════════
## 三、买入/卖出硬性规则
## ═══════════════════════════════════════

### 买入必要条件:
1. 分析师建议为 BUY 且评分 ≥ 7
2. 风控级别为 NORMAL 或 FAVORABLE
3. 账户总仓位未达上限

### 卖出规则:
1. 止损: 股价从买入价下跌 8% 或跌破 SMA50
2. 止盈: 盈利 > 20% 后使用 SMA20 追踪止盈
3. 风险卖出: 风控评分变为 LOCKDOWN

## ═══════════════════════════════════════
## 历史经验与记忆
## ═══════════════════════════════════════

{memory_context}

## ═══════════════════════════════════════
## 四、输出格式
## ═══════════════════════════════════════

```
【账户概览】资产总值 $XXX | 可用现金 $XXX | 总仓位 XX%

【持仓处理】
  XXXX: 盈利 XX% | 决策: HOLD/SELL | 原因: ...

【候选研判】
  XXXX (分析师评分: X):
    决策: BUY XX股 @ $XXX / REJECT
    理由: (结合分析师报告和风控的简述)

【执行状态】已发出交易指令 XXX / 无交易动作
```
"""


class ReActAgent:
    """
    ReAct智能体 v2.0 (Trader/Reduce 角色)
    """

    def __init__(
        self,
        llm: BaseLLM,
        tool_registry: ToolRegistry,
        system_prompt: str = TRADING_SYSTEM_PROMPT,
        max_iterations: int = 10,
        pre_run_tools: Optional[list[str]] = None,
        feishu_notifier=None,
        trading_memory: Optional[TradingMemory] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.llm = llm
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.pre_run_tools = pre_run_tools or []
        self.feishu_notifier = feishu_notifier
        self.trading_memory = trading_memory or get_trading_memory()
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
            action_candidates: 行动候选清单文本（由分析师报告生成）

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

        # 注入历史记忆
        memory_context = ""
        try:
            memory_context = self.trading_memory.get_memory_context()
        except Exception as e:
            self.logger.warning(f"读取交易记忆失败: {e}")

        if memory_context:
            system_prompt = system_prompt.replace("{memory_context}", memory_context)
            self.logger.info(f"已注入交易记忆 ({len(memory_context)} 字符)")
        else:
            system_prompt = system_prompt.replace(
                "{memory_context}",
                "暂无历史经验记录。这是系统初期运行阶段，请严格按照既定规则执行。"
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
                    "请执行本轮交易决策 (Reduce)：\n\n"
                    "1. 巡检现有持仓：检查是否触发止损/止盈规则，或因风控评分需减仓。\n"
                    "2. 评估分析师报告：基于提供的候选标的报告（Map结果），决定是否买入。\n"
                    "3. 仓位计算：如决定买入，必须先计算精准股数，确保不超限。\n"
                    "4. 执行交易：发出 buy/sell 指令。\n\n"
                    "请根据预执行的账户数据和分析师报告开始推理。"
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

        # 记录执行过的工具
        executed_tools: list[str] = []

        # ReAct 循环
        self.logger.info("=" * 50)
        self.logger.info("开始 ReAct 循环 (Trader/Reduce)")

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

                # 并行执行所有工具调用
                def execute_tool(tc):
                    self.logger.info(f"调用工具: {tc.name}")
                    self.logger.info(f"参数: {json.dumps(tc.arguments, ensure_ascii=False)}")

                    try:
                        res = self.tool_registry.execute(tc.name, **tc.arguments)
                        self.logger.info(f"工具返回 [{tc.name}]: {res[:200]}..." if len(res) > 200 else f"工具返回 [{tc.name}]: {res}")
                    except Exception as e:
                        res = json.dumps({"error": str(e)})
                        self.logger.error(f"工具执行错误 [{tc.name}]: {e}")
                    return tc, res

                tool_results = {}
                with ThreadPoolExecutor(max_workers=min(len(response.tool_calls), 3)) as executor:
                    futures = {executor.submit(execute_tool, tc): tc for tc in response.tool_calls}
                    for future in as_completed(futures):
                        tc, res = future.result()
                        tool_results[tc.id] = (tc, res)

                # 按原顺序添加到 messages
                for tool_call in response.tool_calls:
                    executed_tools.append(tool_call.name)
                    tc, result = tool_results[tool_call.id]
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
            content="已达最大步数，请总结当前交易决策并给出最终建议。"
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
