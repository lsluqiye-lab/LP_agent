"""
ReAct Agent v4.5.2 (The Strategic Brain)
A sophisticated Reasoning + Acting framework that orchestrates experts to make 
high-conviction trading decisions based on Narrative, Macro, and Tree-of-Thought Intelligence.
"""
import asyncio
import json
import logging
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

from llm.base import BaseLLM, ChatMessage, Role, LLMResponse, ToolCall
from tools.base import ToolRegistry
from data.memory import TradingMemory, get_trading_memory

# ═══════════════════════════════════════════
# SYSTEM PROMPT v4.5.2 - The Strategic Brain (ToT-Powered Edition)
# ═══════════════════════════════════════════

STRATEGIC_SYSTEM_PROMPT = """你是一个顶级对冲基金的**首席投资官 (CIO)**。你的目标是实现账户净值的长期稳健增长。

### 核心哲学：Bear-Case-First (风险优先)
- **风险证伪**：买入前必须找不到足以推翻趋势的利空因素。
- **趋势包容**：首选 Stage 2 (股价>SMA50>SMA200)；允许 Stage 1 底部放量反转确认后右侧建仓。
- **金字塔建仓**：浮盈是加仓的唯一凭证，绝不摊平亏损。

### 1. 深度思考树协议 (Tree-of-Thought Protocol) - NEW!
针对 **Tier 1 Leader** 或 **高冲突 (Identified Conflicts > 1)** 的标的，你必须放弃直线思维，启用多路径博弈推演：
- **[PATH: BULL] (乐观路径)**：假设趋势成立且叙事共振。寻找支撑逻辑的最强点（如量价配合、阿尔法评分、板块补涨）。
- **[PATH: BEAR] (质疑路径)**：作为“魔鬼代言人”。寻找证伪证据：是否存在缩量突破？RSI 是否顶背离？叙事是否正在边际递减？是否存在重要的压力位阻滞？
- **[PATH: TAIL_RISK] (压力测试)**：假设发生极端偏移（如流动性突发枯竭、日元巨震）。个股的下行安全边际（止损深度）是否足以覆盖这种波动？
- **[PATH: SYNTHESIS] (审判合成)**：对比上述路径。只有当 [BULL] 的期望收益远大于 [BEAR] 证伪的概率，且 [TAIL_RISK] 可控时，才准予执行。

### 2. 叙事与宏观边界 (Narrative & Macro Intelligence)
你必须将 `narrative_briefing` 作为决策的“直觉”第一权重：
- **叙事共振**：符合 `top_narratives` 主题且处于 Stage 2 突破的标的，应视为高信心标的。
- **叙事偏移警告**：若 `narrative_shift_warning` 提示风险激增，即便个股技术面良好，也必须执行避险（减仓或买入 Puts）。

### 3. 量化决策指令 (Quant Intelligence & quant_metadata)
你必须严格遵守 `portfolio_directives` 中的动态红线，并结合 `quant_metadata` 进行仓位管理：
- **Alpha Score Sizing**：
  - 在决策简报中，你会看到由选股神器注入的 `quant_metadata` (包含 score, rank, conviction)。
  - **Score > 85 或 Tier 1 Leader**：视为高信心。分配 **1.5x 标准头寸 (10-15%)**，使用 **3.5x - 4.0x ATR** 止损。
  - **Score < 75 或 Tier 2 Satellite**：视为观察位。限制在 **0.5x - 0.8x 标准头寸 (5%左右)**，使用 **1.5x-2.0x ATR** 止损。
  - **Rank 1-3**：今日最强阿尔法候选，优先占用可用现金流。
- **V-Recovery (纠偏回补)**：满足“价格收复 50% + 2x 成交量 + 48h内”逻辑方可执行。

### 4. 对冲动态退出 (Dynamic Hedge Unwinding) - NEW!
- **Theta 损耗敏感**：对冲期权（Puts）是昂贵的“出血资产”。每持有一天，其时间价值损耗（Theta）都会直接侵蚀净值。你必须像厌恶亏损一样厌恶无意义的 Theta 损耗。
- **强制退出触发**：
  - 当风控评分从 **<70 (LOCKDOWN/CAUTIOUS)** 显著修复至 **>80 (FAVORABLE)** 时，你必须在第一轮迭代中优先评估并执行对冲平仓。
  - **原则**：晴天不打伞。如果宏观风险已解除，持有 Puts 是严重的决策失职。
- **非对称对冲**：在评分 **>75** 的环境下，应优先使用 `TSMPCT` (追踪止损) 作为“零成本保险”，严禁续持或买入昂贵的 Protective Puts。

### 5. 标的池与执行铁律
- **禁买期**：HARD_STOP 3天；SOFT_WEED 1天。
- **禁止无谓微调**：拦截无效微调时（success: False），必须立刻停止尝试。
- **工具调用**：必须显式调用 `buy_stock`/`sell_stock`。

请基于当前上下文，使用 ToT 协议进行深度推演并做出最符合风险收益比的决策。"""





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
        # 获取日志记录器
        from data.trade_logger import get_trade_logger
        self.trade_logger = get_trade_logger()

    async def run(
        self,
        decision_briefings_json: str,
        risk_context: str = "Risk: NORMAL",
        risk_score: float = 50.0,
        portfolio_directives: Optional[Dict] = None,
        pre_executed_data: Optional[Dict] = None,
        interrupt_events: Optional[str] = None
    ) -> str:
        """
        Runs the ReAct loop based on provided expert briefings.
        """
        # 1. Prepare Tools
        tools = self.tool_registry.get_openai_tools()

        # 2. Render Prompt
        memory_context = self.trading_memory.get_memory_context() if self.trading_memory else "No prior history."
        portfolio_str = json.dumps(portfolio_directives, ensure_ascii=False) if portfolio_directives else "无组合级额外限制。"
        
        # 获取今日交易次数
        today_trades = self.trade_logger.get_today_trades()
        trade_count = len(today_trades)
        from config import RiskConfig
        target_trades = RiskConfig.from_env().target_daily_trades

        system_prompt = self.system_prompt.replace("{tools_section}", self._build_tools_section(tools)) \
                                         .replace("{decision_briefings}", decision_briefings_json) \
                                         .replace("{memory_context}", memory_context) \
                                         .replace("{risk_score}", str(risk_score)) \
                                         .replace("{portfolio_directives}", portfolio_str) \
                                         .replace("{trade_count}", str(trade_count)) \
                                         .replace("{target_trades}", str(target_trades))

        # 3. Initialize Messages
        messages = [
            ChatMessage(role=Role.SYSTEM, content=system_prompt)
        ]

        if pre_executed_data:
            data_msg = "Current Account/Position Context:\n" + json.dumps(pre_executed_data, indent=2)
            messages.append(ChatMessage(role=Role.USER, content=data_msg))

        if interrupt_events:
            interrupt_msg = (
                "🚨🚨🚨 HIGH PRIORITY INTERRUPT EVENTS (WATCHDOG) 🚨🚨🚨\n"
                f"你是由高频雷达(Watchdog)强行唤醒的！以下是刚刚发生的盘中异动：\n{interrupt_events}\n"
                "请必须针对上述异动做出回应（如：右侧突破则加仓建仓，动能衰竭则卖出做T锁定利润，跌破重要支撑则果断止损）。你可以自由调度所有工具，如果认为异动是噪音，也可选择忽略。\n"
            )
            messages.append(ChatMessage(role=Role.USER, content=interrupt_msg))

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
                response = await asyncio.to_thread(self.llm.chat, messages, tools=tools)
                current_thought = response.content or ""
                
                if not response.has_tool_calls:
                    import re
                    # ---- Execution Audit Loop (防“嘴炮”拦截网) ----
                    missing_actions = []
                    action_match = re.search(r"Action:\s*(BUY|SELL|ADD|TIGHTEN_STOP)", current_thought, re.IGNORECASE)
                    if action_match:
                        # 检查在此 ReAct 过程中是否真的调用过买卖工具
                        if not any(d['name'] in ['buy_stock', 'sell_stock'] for d in executed_tool_details):
                            missing_actions.append(action_match.group(1).upper())
                    
                    if missing_actions and i < self.max_iterations - 1:
                        self.logger.warning(f"检测到决策幻觉 (Audit Failed): 意图 {missing_actions[0]} 但未调用工具. 强制重试...")
                        messages.append(ChatMessage(
                            role=Role.USER, 
                            content=f"🚨 严重警告：你在最终结论中给出了 `Action: {missing_actions[0]}`，但你**并没有真实调用 `buy_stock` 或 `sell_stock` 工具**！\n仅仅在文本中输出指令是绝对无效的。请你在此轮迭代中立刻调用相应工具，否则你的决策将被视为废弃！"
                        ))
                        continue
                    # ------------------------------------------------

                    self.logger.info("Decision loop complete.")
                    final_content = response.content or "No action taken."
                    
                    if self.feishu_notifier and executed_tool_details:
                        # (保持原有的飞书通知逻辑不变...)
                        chart_info = ""
                        image_key = None
                        try:
                            from tools.visualizer import plot_trade_signal
                            for d in executed_tool_details:
                                if d['name'] in ['buy_stock', 'sell_stock']:
                                    args = d.get('arguments', {})
                                    ticker = args.get('symbol')
                                    price = args.get('price') or 0
                                    action = "BUY" if d['name'] == 'buy_stock' else "SELL"
                                    
                                    plot_path = plot_trade_signal(ticker, action, float(price), final_content)
                                    if plot_path:
                                        img_key = self.feishu_notifier.upload_image(plot_path)
                                        if img_key:
                                            image_key = img_key
                                            chart_info = "\n\n📈 **附交易图表**"
                                        else:
                                            chart_info = f"\n\n📈 **交易图表已保存本地**: `{plot_path}`"
                                        break
                        except Exception as e:
                            self.logger.error(f"生成图表失败: {e}")

                        exec_log = "\n".join([f"✅ **{d['name']}**: {d['result']}" for d in executed_tool_details])
                        self.feishu_notifier.send_card(
                            title="⚡ 交易执行报告",
                            content=f"### 决策逻辑\n{final_content}\n\n### 执行详情\n{exec_log}{chart_info}",
                            color="orange",
                            image_key=image_key
                        )
                        
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
                        result = await asyncio.to_thread(self.tool_registry.execute, tc.name, **tc.arguments)
                        if tc.name in ["buy_stock", "sell_stock"]:
                            executed_tool_details.append({"name": tc.name, "result": result, "arguments": tc.arguments})
                            
                            # ---- 策略审计注入: 实时打脸/回补检测 ----
                            if tc.name == "buy_stock":
                                try:
                                    symbol = tc.arguments.get("symbol")
                                    buy_price = tc.arguments.get("price")
                                    
                                    # 回溯过去3天的日志
                                    past_logs = self.trade_logger.get_recent_logs(days=3)
                                    for plog in past_logs:
                                        p_trades = plog.get("trades", [])
                                        # 寻找最近的一次卖出记录
                                        last_sell = next((t for t in reversed(p_trades) if t.get("symbol") == symbol and t.get("side") == "Sell"), None)
                                        
                                        if last_sell:
                                            sell_price = last_sell.get("price")
                                            if sell_price and buy_price:
                                                friction = (float(buy_price) - float(sell_price)) / float(sell_price)
                                                if friction > 0:
                                                    # 记录审计：发现打脸行为
                                                    self.trade_logger.log_audit(
                                                        audit_type="WHIPSAW",
                                                        symbol=symbol,
                                                        event="RE_ENTRY_DETECTION",
                                                        metrics={
                                                            "sell_price": sell_price,
                                                            "buy_price": buy_price,
                                                            "friction_pct": round(friction * 100, 2),
                                                            "sell_date": plog.get("date")
                                                        },
                                                        improvement=f"检测到打脸回补。卖出日期: {plog.get('date')}，价格摩擦: {round(friction * 100, 2)}%。建议检查是否受情绪驱动或开盘诱多影响。"
                                                    )
                                            break # 只对比最近一次
                                except Exception as ae:
                                    self.logger.error(f"Audit processing error: {ae}")
                            # ----------------------------------------
                            
                            # 记录决策日志
                            try:
                                action_type = "BUY" if tc.name == "buy_stock" else "SELL"
                                symbol = tc.arguments.get("symbol", "UNKNOWN")
                                self.trade_logger.log_decision(
                                    symbol=symbol,
                                    action=action_type,
                                    reasoning={
                                        "thought": current_thought,
                                        "tool_call": f"{tc.name}({tc.arguments})",
                                        "execution_result": result
                                    },
                                    risk_score=risk_score,
                                    approved=True # 如果能执行到这里说明已通过初步校验
                                )
                            except Exception as le:
                                self.logger.error(f"Failed to log decision to trade_logger: {le}")

                    except Exception as e:
                        result = json.dumps({"error": str(e)}, ensure_ascii=False)
                        if tc.name in ["buy_stock", "sell_stock"]:
                            executed_tool_details.append({"name": tc.name, "result": f"FAILED: {str(e)}", "arguments": tc.arguments})
                    
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
