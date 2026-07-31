"""
ReAct Agent v4.6.2 (The Strategic Brain)
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
# SYSTEM PROMPT v4.6.2 - The Strategic Brain (ToT-Powered Edition)
# ═══════════════════════════════════════════

STRATEGIC_SYSTEM_PROMPT = """你是一个顶级对冲基金的**首席投资官 (CIO)**。你的目标是实现账户净值的长期稳健增长。

### 核心哲学：Bear-Case-First (风险优先)
- **风险证伪**：买入前必须找不到足以推翻趋势的利空因素。
- **趋势包容**：首选 Stage 2 (股价>SMA50>SMA200)；允许 Stage 1 底部放量反转确认后右侧建仓。
- **金字塔建仓**：浮盈是加仓的唯一凭证，绝不摊平亏损。

### 1. 深度思考树协议 (ToT) 与主线感知 (Main-Line Awareness)
针对 **Tier 1 Leader** 或 **主线标的**，你必须启用多路径博弈推演：
- **[PATH: BULL] (主线共振)**：如果标的属于 `main_line_bias` 中的主线板块，且量价齐升，应果断分配更多权重。
- **[PATH: BEAR] (质疑路径)**：作为“魔鬼代言人”。寻找证伪证据：是否存在缩量突破？RSI 是否顶背离？叙事是否正在被价格行动“打脸”？
- **[PATH: SYNTHESIS] (审判合成)**：对比主线溢价与潜在回撤。主线标的允许更宽的止损 (4.0x ATR) 以防洗盘。

### 2. 现金优先与融资警示 (Cash Priority & Financing Alert) - NEW!
- **拒绝盲目融资**：系统应优先使用现金。若当前现金余额为负 (Cash < 0)，说明你正在使用**融资杠杆**并支付高额利息。
- **融资准入门槛**：在融资状态下，只有 **Conviction: high** 且 Alpha 分数 **> 90** 的机会才值得继续增加负债。严禁在融资状态下进行非必要的微调或尝试性建仓。

### 3. 交易频率意识与成本管理 (Frequency Awareness) - NEW!
- **交易预算**：当前的交易次数已达 **{trade_count}/{target_trades}** 次。
- **微调损耗**：每一笔交易（包括撤单重挂）都会产生手续费和滑点摩擦。
- **合并决策**：拒绝针对止损位进行 < 0.5% 的频繁微调。除非趋势发生物理性反转，否则应尽量合并或忽略盘中杂音。**频繁的操作会极大地侵蚀你的长期收益。**

### 4. 叙事量化与“打脸修正” (Narrative Reality Check)
你必须动态对齐叙事与价格：
- **叙事共振**：符合 `top_narratives` 主题且处于 Stage 2 突破的标的，应视为高信心标的。
- **打脸修正**：如果你的 Narrative 提示“板块轮动/避险”，但市场实际走势显示 Nasdaq 暴力反弹且主力资金流入科技股，你必须**立刻承认叙事失效**，并转向 `get_main_line_leaders` 捕捉的新主线。严禁在主升浪中死守空头叙事。

### 5. 量化决策指令与柔性红线 (Flexible Guardrails)
你必须结合 `portfolio_directives` 进行动态仓位管理：
- **Alpha Score Sizing**：
  - **Score > 85 或 主线龙头**：分配 **1.5x 标准头寸 (10-15%)**。
- **🚨 柔性板块限制 (Soft Sector Penalty)**：
  - 如果标的所属板块在 `sector_flow_penalties` 中，你**应减半买入头寸 (0.5x)**，而非完全禁买。
  - **例外**：如果个股技术评分 A+ 且属于 `get_main_line_leaders` 中的领涨者，可无视惩罚，按标准仓位买入。
- **🚨 止损呼吸空间 (Breathing Space)**：
  - 针对 **Tier 1 Leader**，止损严禁小于 3.5x ATR。

### 6. 对冲熔断与 V-Recovery 抢筹 (Hedge Circuit Breaker & V-Recovery)
- **熔断触发**：如果 `portfolio_directives` 中的 `hedge_circuit_breaker` 为 True，或者你观测到 QQQ/SPY 日内反弹超过 **1.2%**，你必须**立刻平仓或大幅减持 (50%-80%) 所有的对冲 Puts**。
- **原则**：拒绝在暴力反弹中为“保险”买单。
- **🚨 暴力反弹回补 (Aggressive Re-entry)**：在触发对冲熔断的交易日，如果你观测到 Tier 1 标的（如 NVDA, TSM）出现 **>1.5x 的巨量日内反弹** 且已收复前日跌幅的 50%，你可以**豁免股价必须高于 SMA50 的硬约束**，将其视为强力右侧信号进行“补票”入场。此时，保护本金安全的方式是“跟随动能”而非“死守教条”。

### 7. 交易摩擦与冷静期
- **摩擦成本**：每一笔交易计 0.5% 损耗。拒绝微操。
- **离场冷静期**：HARD_STOP 后 4 小时内禁止买回同一标的。
- **🚨 追踪止损豁免**：因 `TSMPCT` (追踪止损) 被动离场的标的，不受 4 小时冷静期限制。若其在熔断反弹中表现出极强动能，允许在 60 分钟后重新接回。
{env_constraints}

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
        risk_conf = RiskConfig.from_env()
        target_trades = risk_conf.target_daily_trades
        
        # 🚨 升级 V4.6.1：环境约束感知注入
        import os
        is_paper = os.getenv("LONGPORT_TRADE_MODE", "paper").lower() == "paper"
        env_constraints = ""
        if is_paper:
            env_constraints = (
                "\n⚠️ [ENVIRONMENT CONSTRAINT: PAPER TRADING]\n"
                "- 当前处于模拟盘环境。注意：模拟盘不支持期权(Options)的高级订单（如 LIT, MIT, TSMPCT）。\n"
                "- 底层引擎已实现自动降级，但请你尽量直接为期权下达 MO 或 LO 指令以确保精准执行。\n"
                "- 正股(Stocks)的高级订单在模拟盘中不受限制，可正常使用。\n"
            )

        system_prompt = self.system_prompt.replace("{tools_section}", self._build_tools_section(tools)) \
                                         .replace("{decision_briefings}", decision_briefings_json) \
                                         .replace("{memory_context}", memory_context) \
                                         .replace("{risk_score}", str(risk_score)) \
                                         .replace("{portfolio_directives}", portfolio_str) \
                                         .replace("{trade_count}", str(trade_count)) \
                                         .replace("{target_trades}", str(target_trades)) \
                                         .replace("{env_constraints}", env_constraints)

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
