"""
ReAct Agent v3.0 (The Strategic Brain)
A sophisticated Reasoning + Acting framework that orchestrates experts to make 
high-conviction trading decisions based on the 'Risk-First' philosophy.
"""
import json
import logging
import asyncio
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

from llm.base import BaseLLM, ChatMessage, Role, LLMResponse, ToolCall
from tools.base import ToolRegistry
from data.memory import TradingMemory, get_trading_memory

# ═══════════════════════════════════════════
# SYSTEM PROMPT v3.0 - The Strategic Brain
# ═══════════════════════════════════════════

STRATEGIC_SYSTEM_PROMPT = """你是一个顶级对冲基金的**首席投资官 (CIO)**。
你的任务是根据**宏观风控环境**、**账户状态**以及**多专家决策简报 (Decision Briefing)** 做出最终的交易裁决。

你的核心哲学是：**Bear-Case-First (风险优先)**。
在决定买入前，你必须首先证伪所有的风险因素。看到确定性机会时，你必须果断。

## 你的能力 (Tools)
{tools_section}

## ═══════════════════════════════════════
## 一、 决策简报 (Expert Input)
## ═══════════════════════════════════════

系统已为你准备了针对候选标的的**专家简报**：

{decision_briefings}

## ═══════════════════════════════════════
## 二、 你的推理逻辑 (ReAct Loop)
## ═══════════════════════════════════════

在每一轮思考 (Thought) 中，你必须遵循以下步骤：

1.  **多维质疑 (Skeptical Analysis)**: 不要简单接受专家简报的结论。如果三个专家的观点过于一致，请搜索最新的反面观点（Bear Case）。
2.  **识别并深度调查矛盾 (Deep Investigation)**: 观察 `identified_conflicts` 字段。**如果专家意见有任何分歧，或者宏观环境与个股表现不符，你必须通过 `search` 工具至少进行 1-2 轮深度调查，挖掘分歧背后的催化剂。**
3.  **验证硬指标 (Rule Validation)**:
    *   **买入必要条件**: 趋势必须是 **Stage 2**。股价必须在 50 日和 200 日均线之上。
    *   **风控约束**: 宏观风控分数低于 50 时禁止新建仓。
4.  **计算风险报酬比 (R/R Ratio)**: 基于支撑位和阻力位，评估你的止损空间和获利目标。
5.  **不草率结论**: 除非所有关键疑点都已澄清，否则不要停止思考。一个好的决策通常需要 3-5 轮的工具调用来验证数据。


## ═══════════════════════════════════════
## 三、 交易执行准则
## ═══════════════════════════════════════

### 🔷 买入操作
- 调用 `buy_stock`。
- 必须预先计算仓位：金额 = min(可用现金 * 单笔上限, 总资产 * 1.5% / 预期止损%)。
- 止损建议：股价下跌 8% 或跌破 SMA50。

### 🔷 卖出操作
- 止损、止盈或宏观环境恶化 (LOCKDOWN) 时强制执行。
- 优先处理现有持仓的减仓/清仓需求。

## ═══════════════════════════════════════
## 历史经验与记忆
## ═══════════════════════════════════════

{memory_context}

## ═══════════════════════════════════════
## 四、 输出格式
## ═══════════════════════════════════════

【账户状态】资产 $XXX | 现金 $XXX | 仓位 XX%

【矛盾点辩解】(解释你如何看待简报中的冲突，以及你调查后的结论)

【交易决策】
- Symbol: XXXX
- Action: BUY/SELL/HOLD
- Quantity: XX
- Reason: (基于风险优先原则的深度理由)

【指令状态】已发出指令 / 无操作
"""

class ReActAgent:
    """
    The Strategic Brain of LP-Agent.
    Orchestrates specialized experts and executes decisions.
    """

    def __init__(
        self,
        llm: BaseLLM,
        tool_registry: ToolRegistry,
        system_prompt: str = STRATEGIC_SYSTEM_PROMPT,
        max_iterations: int = 10,
        feishu_notifier=None,
        trading_memory: Optional[TradingMemory] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.llm = llm
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.feishu_notifier = feishu_notifier
        self.trading_memory = trading_memory or get_trading_memory()
        self.logger = logger or logging.getLogger(__name__)

    async def run(
        self,
        decision_briefings_json: str,
        risk_context: str = "Risk: NORMAL",
        pre_executed_data: Optional[Dict] = None
    ) -> str:
        """
        Runs the ReAct loop based on provided expert briefings.
        """
        # 1. Prepare Tools
        tools = self.tool_registry.get_openai_tools()

        # 2. Render Prompt
        memory_context = self.trading_memory.get_memory_context() if self.trading_memory else "No prior history."
        
        system_prompt = self.system_prompt.replace("{tools_section}", self._build_tools_section(tools)) \
                                         .replace("{decision_briefings}", decision_briefings_json) \
                                         .replace("{memory_context}", memory_context)

        # 3. Initialize Messages
        messages = [
            ChatMessage(role=Role.SYSTEM, content=system_prompt)
        ]

        if pre_executed_data:
            data_msg = "Current Account/Position Context:\n" + json.dumps(pre_executed_data, indent=2)
            messages.append(ChatMessage(role=Role.USER, content=data_msg))

        messages.append(ChatMessage(
            role=Role.USER, 
            content="Please review the Decision Briefings and execute the necessary trading actions. Prioritize risk investigation."
        ))

        # 4. ReAct Loop
        executed_tool_details = []
        self.logger.info("Starting Strategic ReAct Loop...")

        for i in range(self.max_iterations):
            self.logger.info(f"Iteration {i+1}/{self.max_iterations}")
            
            try:
                response = self.llm.chat(messages, tools=tools)
                
                if not response.has_tool_calls:
                    self.logger.info("Decision loop complete.")
                    final_content = response.content or "No action taken."
                    
                    if self.feishu_notifier and executed_tool_details:
                        # 构造增强版交易通知
                        results_str = "\n".join([f"✅ 执行结果: {d['name']} -> {d['result']}" for d in executed_tool_details])
                        msg = f"⚡ 【交易执行报告】\n\n{final_content}\n\n{results_str}"
                        self.feishu_notifier.send_text(msg)
                        
                    return final_content

                # Process Tool Calls
                messages.append(ChatMessage(
                    role=Role.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                    native_content=response.native_content
                ))

                for tc in response.tool_calls:
                    self.logger.info(f"Executing: {tc.name}({tc.arguments})")
                    
                    try:
                        result = self.tool_registry.execute(tc.name, **tc.arguments)
                        if tc.name in ["buy_stock", "sell_stock"]:
                            executed_tool_details.append({"name": tc.name, "result": result})
                    except Exception as e:
                        result = json.dumps({"error": str(e)})
                        if tc.name in ["buy_stock", "sell_stock"]:
                            executed_tool_details.append({"name": tc.name, "result": f"FAILED: {str(e)}"})
                    
                    messages.append(ChatMessage(
                        role=Role.TOOL,
                        content=result,
                        tool_call_id=tc.id,
                        name=tc.name
                    ))

            except Exception as e:
                self.logger.error(f"ReAct loop error: {e}")
                return f"Error: {str(e)}"

        return "Reached max iterations without a final conclusion."

    def _build_tools_section(self, available_tools: list[dict]) -> str:
        # Simplified tool section builder
        lines = []
        for t in available_tools:
            name = t["function"]["name"]
            desc = t["function"].get("description", "")
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)
