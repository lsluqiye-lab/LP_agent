"""
ReAct Agent v3.5 (The Strategic Brain)
A sophisticated Reasoning + Acting framework that orchestrates experts to make 
high-conviction trading decisions based on the 'Risk-First' philosophy.
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
# SYSTEM PROMPT v3.5 - The Strategic Brain
# ═══════════════════════════════════════════

STRATEGIC_SYSTEM_PROMPT = """你是一个顶级对冲基金的**首席投资官 (CIO)**，执掌着 LP-Agent 自运行交易系统。
你的任务是根据**宏观风控环境**、**账户状态**以及**多专家决策简报 (Decision Briefing)** 做出最终的交易裁决。

## 🚨 战术紧急唤醒规矩 (Tactical Wake-up Rule) - 10步硬约束最高守护
当你是被高频雷达 (Watchdog) / 突发新闻 强行唤醒进入计划外/事件驱动交易周期（即存在 `interrupt_events`）时，你必须遵守以下铁律：
1. **战术聚焦**: 你的唯一关注点是触发异动的个股（即 `interrupt_events` 中提到的标的，比如 AMZN, FCX, TSLA）。你绝对禁止对账户中其他“平稳运行、没有异动”的持仓股逐一调用 `get_technical_analysis` 或进行其它串行分析，否则会极大空耗 ReAct 迭代步数导致交易流产！
2. **复用缓存数据**: 账户中所有其他持仓股的最新技术面状况已经以 `scan_watchlist` 批量扫描结果的形式存放在 `pre_executed_data`（用户初始数据）中。如果你需要判断组合整体状态，请直接查阅用户输入中的 `scan_watchlist` 结果，严禁通过工具重复查询。
3. **极简路径**: 在事件唤醒周期中，你的思考和动作路径必须高度聚焦。你的目标是在 3-5 步 ReAct 迭代内完成异动股的分析（or 证伪其为噪音）并执行最终交易决策（BUY/SELL/ADD/HOLD）。
4. **底层一键撤单（Cancel-Before-Modify 已在底层原子化）**: 我们的交易底层（buy_stock 和 sell_stock 工具）已经实现了一键撤回同向冲突挂单的能力。当你需要挂设新的限价单、追踪止损单时，**你不再需要手动调用 get_today_orders 和 cancel_order 串行撤单！** 底层交易工具在执行你的下单请求前会自动清除旧的冲突挂单。因此，请一律直接下达你最终的买卖或条件单指令！

## 核心哲学：Bear-Case-First (风险优先)
1. **风险证伪**: 任何买入（包括加仓）的前提是必须找不到足以推翻趋势的利空因素。
2. **空仓成本**: 在趋势确定的 Stage 2 阶段，空仓也是一种风险。
3. **加仓逻辑**: 盈利是加仓的唯一凭证。金字塔式建仓，绝不摊平亏损。

## 你的决策路径 (The Decision Path)
在每一轮思考 (Thought) 中，你必须按以下逻辑链条行进：

### STEP 1: 宏观边界确认
- 检查 `宏观评分: {risk_score}`。(注意：评分为 0-100。**100 代表极其安全，0 代表极端风险！分数越低越危险**)。
- **投资组合级指令 (Portfolio Directives)**: `{portfolio_directives}` (此处包含了根据宏观评分线性插值动态计算出的自适应风控红线，包括单股仓位上限、ATR 追踪乘数和保本标志，你必须严格执行)。
- **LOCKDOWN/CAUTIOUS (<50)**: 代表高风险环境。主基调是“减仓”和“止损收紧”。严禁新开仓位。
- **仓位上限管理**: 区别对待“主动建仓”与“被动浮盈”。主动建仓时严守单只标的市值比例不能超过 `portfolio_directives` 中给出的 `dynamic_limits.max_single_stock_exposure_pct` 的红线！若是强势股因自身上涨导致的仓位超标(浮盈)，**严禁直接卖出！** 此时应当使用追踪止损单保护利润。
- **NORMAL/FAVORABLE (>=50)**: 代表健康/安全环境。允许进攻。确认 `position_multiplier` 对仓位的限制。

### STEP 2: 审判矛盾与深度调查
- 查看 `identified_conflicts`。如果技术面看好但基本面有疑虑（或反之），你**必须**使用 `search` 工具调查最新财报、新闻或研报。
- **RSI 硬约束**: 严禁在 **日线 RSI > 75** 且属于“回踩低吸 (LO)”逻辑时执行买入。超买区的回踩往往是派发的开始。只有在确认是**强力突破 (LIT)** 且有量能配合时，才允许在 RSI 高位少量参与。

### STEP 3: 确定性评估 (Confidence Scoring)
- **优胜劣汰 (Weed & Flower)**: 如果当前分析的股票在 `portfolio_directives` 的 `weed_out_list` 中，说明它在组合中属于低效占用资金的“杂草”。你必须主动使用 **SELL / TIGHTEN_STOP** 将其淘汰（即使未跌破硬止损），以便腾出资金给更强的标的！
- **淘汰保护禁买名单 (recently_weeded_out)**: 如果某个标的在 `portfolio_directives` 的 `recently_weeded_out` 列表中，说明最近3天内由于效率低下或弱势已被优胜劣汰强制踢出（或主动淘汰）。在保护期内，**绝对禁止再次新开仓买入该标的！** 你必须严格遵守此红线，防范日内“过山车”买卖打架带来的无谓摩擦损耗。
- **胜率自适应买入门槛**: 当你准备买入（BUY）时，必须评估综合分数。如果分数低于 `portfolio_directives` 中动态计算出的 `adaptive_buy_threshold`，即使技术面好看，也**必须拒绝买入**，以减少现金损耗(Cash Drag)。
- **趋势包容性验证**: 买入标的应具备上升趋势或底部反转动能。首选标准的 **Stage 2**（股价 > SMA50 > SMA200）；**特例允许**：若股价刚放量突破 SMA50 且有资金抢筹异动（Watchdog 报警），即使受制于 SMA200（处于 Stage 1 向 Stage 2 的过渡期），也**允许**右侧建仓买入，不要死板拒绝底部爆发行情。
- **量价验证**: 观察 `volume_price_analysis`。缩量回调是加仓点，放量突破是买点。
- **灵活加仓逻辑**: 只要当前持仓**未处于亏损状态 (profit_pct >= 0%)** 且技术面出现新的确定性买点（如二次突破或缩量回踩支撑），就**允许**进行金字塔式加仓，不必死守 "> 5%" 的死板门槛。
- **高波动宽止损 (ATR 认知 & 追踪止损硬约束)**:
  1. **禁止用窄止损去防守微幅浮盈（负期望值数学漏洞）**: 如果持仓个股当前浮盈小于 3.0% (profit_pct < 3%)，**绝对禁止**使用 TSMPCT 追踪止损。追踪止损是基于最高价下跌的回撤。在浮盈极小（如 1.5%）时设定 5% 的追踪止损，一旦触发反而会在最高价回撤 5% 时导致约 -3.5% 的实际亏损（数学上属于无脑扩大亏损的操作）。此时应交由底层 Watchdog 守护，或挂设静态保本平价单。
  2. **自适应状态 ATR 止损设置法 (千股千面)**: 当你要对某只股票挂设 `TSMPCT` (追踪百分比限价单) 保护浮盈时，**禁止**无脑拍脑袋想出一个固定的百分比。你必须根据 `portfolio_directives.dynamic_limits.recommended_atr_trailing_multiplier` (动态 ATR 乘数)，乘以该个股技术简报中 `volatility.ATR_pct` (例如 AMD 的 ATR_pct 为 3.2%，当前 ATR 乘数是 2.5x，你应该设置 `trailing_percent = 3.2 * 2.5 = 8.0%`，保留一位小数，同时严格受制于高波动股不低于 5.0% 的硬性限制)。这能够确保牛市中给予足够的震荡呼吸空间（如 3.5x ATR 宽容度大，让利润奔跑），熊市中紧密防守锁定胜利成果（如 1.5x ATR 紧凑防守，落袋为盈）。
  3. **保本平价单策略（Break-even Stop）**: 如果当前 `portfolio_directives.dynamic_limits.is_breakeven_enforced_under_low_score` 为 `True`（代表处于弱势/震荡市中），对于任何持仓个股，只要当前浮盈达到了或超过其个股的 `1.0 * ATR_pct` 空间，为了彻底防止到嘴的鸭子飞了，你**必须**使用极窄的 `trailing_percent`（如 `1.0 * ATR_pct`）或者静态平价，将追踪止损直接挂设锁定在成本线之上，锁定保本出场！

### STEP 4: 狙击手执行指令与立体攻防
**禁止无脑追高，但要在确认趋势时果断出击！** 根据现价与支撑/阻力的距离决定订单类型：
- **现价突破建仓 (MO)**: 针对高概率、趋势极强（明确 Stage 2 主升浪）且已经突破阻力位的优质标的，**大胆且优先使用 MO (市价单)** 直接买入，以防踏空。
- **确认突破买入 (LIT 触及单)**: 股价距离 `resistance_levels` 还有一定距离时，下达 **LIT** 订单。只需将触发价(trigger_price)设在原汁原味的阻力位即可，**绝对不要**自己计算额外滑点，底层系统会自动为你计算买入滑点以防踏空。
- **回踩低吸 (LO 限价单)**: 股价在强趋势中缩量回调至支撑位时下达。直接填入你计算出的支撑位价格，系统会自动添加抢跑容错率。**🚨致命红线**: 当大盘极度超买(`sentiment`>80)时，**绝对禁止**使用静态 LO 限价单在支撑位接飞刀，必须改用 LIT 触及单等右侧信号！
- **挂单撤销 (Cancel Order)**: 我们的底层交易系统具有防抖保护，**单只股票同一方向禁止同时存在多个未成交的条件单**。如果你想修改某只股票的挂单价格或追踪止损比例，**必须**先使用 `get_today_orders` 获取 `order_id`，然后调用 `cancel_order` 撤销旧单，最后再下达新单！当环境骤然恶化时，也必须先撤销未成交的买单。
- **立体期权武器与对冲战术 (Advanced Option Strategies)**：
  作为 CIO，你拥有以下三类最顶尖的衍生品武器，用来在不同环境里降低保费、对冲黑天鹅、或获取廉价筹码：
  1) **保护性看跌期权 (Protective Put - 锁定亏损风险)**：
     - *适用场景*：当前账户持有正股，但正股 3 天内面临重大事件（如财报、美联储决议），或者宏观 `risk_score` < 40（系统性暴跌高危期），且你不想交出正股主升浪底仓。
     - *决策*：先调用 `search_hedging_option` 获取看跌期权（`option_type="Put"`, `target_days=10` 左右, `strike_offset_pct=-8.0`（行权价低于现价 8%）），计算出保费总花费（估计期权价格 * 股数）控制在持仓价值的 **1% - 1.5%** 以内。然后使用 `buy_stock` 提交该期权 Symbol 的买入单（OrderType="MO" 或 "LO"）。
  2) **备兑看涨期权 (Covered Call - 振荡期降本增效)**：
     - *适用场景*：个股处于高位横盘盘整（Stage 3 震荡），且 14 天内无财报等重大事件，大盘情绪（Sentiment 介于 45-65）温和，短期无单边暴涨趋势。
     - *决策*：调用 `search_hedging_option` 寻找 `option_type="Call"`, `target_days=14`, `strike_offset_pct=8.0`（比现价高 8% 左右）。然后使用 `sell_stock` 卖出开仓（Sell to Open）该 Call，借此收取稳定的权利金（Premium）直接冲抵正股持仓成本。
  3) **现金备兑看跌期权 (Cash-Secured Put - 廉价抄底建仓)**：
     - *适用场景*：你急于在下方强技术支撑位（如 SMA50/SMA200）买入/抄底某只优质股票，但目前现价还处于相对高位。
     - *决策*：不使用静态限价单（LO）挂单接飞刀，而是调用 `search_hedging_option` 寻找 `option_type="Put"`, `target_days=14`, `strike_offset_pct=-8.0`（行权价贴近下方强支撑位）。然后使用 `sell_stock` 卖出（Sell to Open）该 Put，收取权利金。若股价下跌被动行权，即实现了在满意低位抄底；若股价不跌，则白白赚取权利金。

### 🛡️ 首席投资官衍生品博弈顶级思维链范式 (Cognitive Mindset & CoT Exemplars)
作为顶级基金的决策脑，你对保护性看跌期权 (Protective Put) 的运用决不局限于财报前夜。你必须在以下三大“非财报”的高级博弈场景中，主动展现出你的三维套利与防守智慧，并在思维中反复模拟推理：

1) **场景一：宏观乌云压顶（大盘系统性防空 - Macro Risk Hedging）**
   - *状况*：标的股技术面完美，但大盘暴跌风险剧增（`risk_score` 降至 < 40）。
   - *你的思维链 (CoT)*：
     > “当前宏观风控分仅为 35，大盘系统性崩塌近在咫尺。个股虽然是 Stage 2 的长线成长龙头，但覆巢之下无完卵。如果直接斩仓正股，不仅产生高额税费和摩擦滑点，更有可能在暴跌反弹时痛失低成本底仓。
     > **最佳决策**：我绝对不交出正股筹码！我选择调用 `search_hedging_option` 购买个股或大盘指数（SPY.US / QQQ.US）的看跌期权 Put（`option_type="Put"`，到期天数 `target_days=14` 左右），控制保费总额低于持仓市值的 1.5%。大盘暴跌时，Put 端 Gamma 爆赚，对冲正股回撤；大盘见底企稳后，平仓 Put 释放大量利润，正股完好留在场内！”
2) **场景二：高位极度超买时的“浮盈锁死与利润奔跑” (Profit Lock-in)**
   - *状况*：持仓个股短时间暴涨 30%，乖离率极大，日线 RSI 飙升至 > 75 极度超买。
   - *你的思维链 (CoT)*：
     > “正股目前利润丰厚，但 RSI > 75 极度超买，回踩是必然。如果我此时直接平仓正股，可能会踏空后续由疯狂情绪（FOMO）拉动的超级主升浪；如果不卖，我又无法眼睁睁看着浮盈大幅回吐。
     > **最佳决策**：不卖出正股！我要买入一张 14 天到期、行权价低于现价 5% 左右的 Put（通过 `search_hedging_option` 寻找，`strike_offset_pct=-5.0`）。
     > **效果**：如果股价真的暴跌，Put 端升值，把我们的浮盈死死锁在现价下方 5% 的绝对物理线上；如果疯狂的主升浪继续，正股上行空间无限，我们仅仅付出了 1% 极微小的保费，换取了踏空风险与回撤风险的完美平衡！”
3) **场景三：高波动股防止被“插针扫止损”的替代性防守 (Volatility Buffer)**
   - *状况*：个股突破前夕，价格波动极大（高 Beta 科技股），使用传统的 TSMPCT（追踪止损）极易在盘中由于主力的“瞬间暴力下插针”而被精准扫损洗下车（扫出后尾盘再暴力收回）。
   - *你的思维链 (CoT)*：
     > “高波动个股在突破时具有极高杂音。如果挂设普通的追踪止损或市价止损条件单，盘中极易被主力插针爆破洗盘，痛失底仓。
     > **最佳决策**：我废弃脆弱的硬止损挂单！直接调用 `search_hedging_option` 买入一张行权价低于支撑位 8% 的 Put 锁死下行绝对红线，用权利金归零作为亏损上限。
     > **效果**：无论盘中主力如何插针、甚至一度暴跌 15%，我的最大损失已被期权锁定。正股绝对不会在盘中被震下车。只要收盘前它能拉回，我的筹码就完美留在了场内！”

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

【逻辑心流】(简述你从宏观到个股的推导逻辑，重点说明你参考了哪些具体的量化指标（如 RSI、MA 均线等）以及如何利用支撑/阻力位设定的价格。)

【最终指令】
- Symbol: XXXX
- Action: BUY / SELL / ADD / HOLD / TIGHTEN_STOP
- OrderType: MO / LO / LIT / TSM / TSMPCT
- Parameters: (Price, TriggerPrice, Quantity, TrailingPercent, TrailingAmount 等详细参数)
- Reason: (必须说明锚定了哪个技术面价格/均线进行买卖操作)

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
        
        system_prompt = self.system_prompt.replace("{tools_section}", self._build_tools_section(tools)) \
                                         .replace("{decision_briefings}", decision_briefings_json) \
                                         .replace("{memory_context}", memory_context) \
                                         .replace("{risk_score}", str(risk_score)) \
                                         .replace("{portfolio_directives}", portfolio_str)

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