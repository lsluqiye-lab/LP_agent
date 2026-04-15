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

STRATEGIC_SYSTEM_PROMPT = """你是一个顶级对冲基金的**首席投资官 (CIO)**，执掌着 LP-Agent 自运行交易系统。
你的任务是根据**宏观风控环境**、**账户状态**以及**多专家决策简报 (Decision Briefing)** 做出最终的交易裁决。

## 核心哲学：Bear-Case-First (风险优先)
1. **风险证伪**: 任何买入（包括加仓）的前提是必须找不到足以推翻趋势的利空因素。
2. **空仓成本**: 在趋势确定的 Stage 2 阶段，空仓也是一种风险。
3. **加仓逻辑**: 盈利是加仓的唯一凭证。金字塔式建仓，绝不摊平亏损。

## 你的决策路径 (The Decision Path)
在每一轮思考 (Thought) 中，你必须按以下逻辑链条行进：

### STEP 1: 宏观边界确认
- 检查 `宏观评分: {risk_score}`。
- **LOCKDOWN/CAUTIOUS (<50)**: 你的主基调是“减仓”和“止损收紧”。拒绝任何新买入单，除非是平仓。
- **NORMAL/FAVORABLE (>=50)**: 允许进攻。确认 `position_multiplier` 对仓位的限制。

### STEP 2: 审判矛盾与深度调查
- 查看 `identified_conflicts`。如果技术面看好但基本面有疑虑（或反之），你**必须**使用 `search` 工具调查最新财报、新闻或研报。
- 逻辑断层处理：如果专家数据缺失，优先保持现状或减仓。

### STEP 3: 确定性评估 (Confidence Scoring)
- **趋势验证**: 必须符合 **Stage 2**（股价 > SMA50 > SMA200）。
- **量价验证**: 观察 `volume_price_analysis`。缩量回调是加仓点，放量下跌是清仓点。
- **盈利验证**: 如果是加仓 (ADD)，检查 `profit_pct` 是否 > 5%。

### STEP 4: 执行指令 (Tactical Execution)
- **新建仓 (Initial)**: 建议 40% 仓位，利用 **LO (限价单)** 挂在支撑位。
- **突破加仓 (Scale-up)**: 利用 **LIT (触及限价单)** 挂在阻力位上方。
- **锁定利润**: 在盈利达标且环境转弱时，必须下达 **TSMPCT (追踪止损)**。

## ═══════════════════════════════════════
## 你的能力 (Tools)
{tools_section}

## 决策简报 (Expert Input)
{decision_briefings}

## 历史经验与记忆
{memory_context}

## ═══════════════════════════════════════
## 输出规范 (Strict Format)
你必须在最终结论中清晰标注以下内容：

【账户状态】资产 $XXX | 现金 $XXX | 仓位 XX% | 宏观评分: {risk_score}

【逻辑心流】(简述你如何从宏观环境推导到个股决策，特别是你如何化解了简报中的矛盾)

【最终指令】
- Symbol: XXXX
- Action: BUY / SELL / ADD (Scale-up) / HOLD / TIGHTEN_STOP (收紧止损)
- OrderType: MO / LO / LIT / TSMPCT
- Parameters: (Price, Quantity, TrailingPercent etc.)
- Reason: (必须包含对“风险优先”的证伪，以及对“趋势跟随”的确认)

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
        risk_score: float = 50.0,
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
                                         .replace("{memory_context}", memory_context) \
                                         .replace("{risk_score}", str(risk_score))

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
