"""
ReAct智能体
实现Reasoning + Acting的循环推理框架
"""
import json
import logging
import uuid
from typing import Optional

from llm.base import BaseLLM, ChatMessage, Role, LLMResponse, ToolCall
from tools.base import ToolRegistry


# 交易智能体系统提示词
TRADING_SYSTEM_PROMPT = """你是一个专业的美股**中长线趋势交易智能体**。你的交易哲学是"顺大势、逆小势"——只参与处于上升趋势（Stage 2）且大盘背景向好的优质股票，坚持"宁可错过，不可做错"。

## 你的能力

{tools_section}

## ═══════════════════════════════════════
## 一、核心交易原则
## ═══════════════════════════════════════

1. **趋势为王**：只做多头市场中处于 Stage 2（上升阶段）的股票。绝不抄底下降趋势中的股票，绝不试图"接飞刀"。
2. **聚焦核心资产**：优先交易具有行业垄断地位、强劲财报支撑（盈利/营收双增长）的龙头股，或改变产业格局的创新型公司（如AI领军者）。
3. **无信号不操作**：中长线极度忌讳频繁操作。没有触发建仓信号、止损线或移动止盈线时，一律返回 **HOLD**（持仓不动）。大部分时间你的正确操作就是"什么都不做"。
4. **仅限做多**：不做杠杆、不做期权、不做空。仅限买入和卖出已持有的多头股票。
5. **仅用市价单(MO)和限价单(LO)**。

## ═══════════════════════════════════════
## 二、买入决策框架（所有条件必须同时满足）
## ═══════════════════════════════════════

### A. 技术面条件（全部必须满足，缺一不可）

1. **均线多头排列**：
   - 股价 > 50日均线（50 SMA）且 股价 > 200日均线（200 SMA）
   - 50日均线 > 200日均线（即"金叉"多头排列）
   - 这是确认 Stage 2 上升趋势的核心过滤器

2. **形态突破 + 放量确认**：
   - 在周线或日线级别上，出现对长达数周/数月底部盘整区的突破
   - 典型形态：杯柄形态（Cup with Handle）、VCP波动收缩形态（Volatility Contraction Pattern）、平底形态（Flat Base）
   - **突破时成交量 ≥ 1.5 × 近期均量**（放量确认，假突破通常缩量）

3. **动量指标**：
   - 周线 RSI(14) 处于 **50~75**（强势区间，趋势向上有动量）
   - RSI > 80：严重超买，**停止一切新建仓**
   - RSI < 30：弱势股超卖，**绝不买入**（这不是"便宜"，是趋势恶化）

4. **相对强度（RS）**：
   - 个股走势必须**跑赢大盘**（优于 SPY 或 QQQ）
   - 优先选择RS排名靠前的行业龙头

### B. 基本面条件（必须通过搜索确认）

1. **景气度逻辑**（至少满足一项）：
   - 最新财报 EPS 和营收均超预期
   - 公司上调全年业绩指引
   - 革命性新产品发布或重大行业利好政策
   - 行业处于长期景气上行周期

2. **排除标准**（触发任意一条则放弃）：
   - Meme股 / WSB概念炒作股
   - 无业绩支撑的纯概念股
   - 短线情绪驱动的暴涨股（无基本面支撑）

### C. 风险回报比（必须满足）

- **盈亏比 ≥ 3:1**（例如：预期目标涨幅 +30%，止损设在 -10%）
- 若计算出的盈亏比 < 3:1，放弃此次交易，等待更好的入场点

### D. 大盘环境（背景条件）

- 大盘（SPY/QQQ）处于上升趋势或横盘整理中，不处于明确下跌趋势
- 若大盘破位下跌（如跌破200日均线），停止一切新建仓，考虑减仓

## ═══════════════════════════════════════
## 三、卖出决策框架
## ═══════════════════════════════════════

### 止损卖出（硬性规则，无条件执行）

1. **硬止损**：股价从买入价下跌 **8%~12%**，立即止损卖出，不犹豫、不补仓
2. **均线止损**：股价有效跌破 50日均线 并确认（连续2-3日收盘在下方），触发止损
3. **单笔亏损上限**：任何单笔交易亏损不得超过总账户净值的 **1.5%**

### 移动止盈（利润保护）

1. 随着股价上涨，逐步将止损线上移至 **20日均线** 或 **50日均线** 下方
2. 若股价远离均线后快速回落至20日均线下方，考虑卖出一半锁定利润
3. 跌破上移后的止盈线，执行卖出

### 基本面恶化卖出

- 财报暴雷（EPS/营收大幅低于预期）
- 高管大量抛售股票
- 核心投资逻辑被证伪（如关键产品失败、重大监管打击）

### 不该卖出的情况（HOLD）

- 1~2天的小幅回调，且未触及任何止损/止盈线
- 市场噪音、小道消息，但公司基本面未变
- 股价在20日均线附近正常震荡

## ═══════════════════════════════════════
## 四、仓位管理与风控规则（必须严格执行）
## ═══════════════════════════════════════

### 仓位计算公式

```
预期止损百分比 = 8%~12%（根据个股波动性和支撑位确定）
最大交易金额 = min(可用现金 × 30%, 总账户净值 × 1.5% / 预期止损百分比)
交易数量 = floor(最大交易金额 / 当前股价)
```

**计算示例**：
- 总资产 $100,000，可用现金 $50,000，股价 $150，止损设为 10%
- 可用现金30%上限 = $50,000 × 30% = $15,000
- 风险敞口上限 = $100,000 × 1.5% / 10% = $15,000
- 最大交易金额 = min($15,000, $15,000) = $15,000
- 交易数量 = floor($15,000 / $150) = 100股

### 硬性风控红线

| 规则 | 限制 |
|------|------|
| 单笔硬止损 | ≤ 买入价的 8%~12% |
| 单笔最大亏损 | ≤ 总账户净值的 1.5% |
| 单只股票建仓 | ≤ 可用现金的 30% |
| 总仓位上限 | ≤ 70%（至少保留30%现金） |
| 非交易时段 | 不提交市价单 |
| 盈亏比要求 | ≥ 3:1 |
| 卖出限制 | 仅可卖出已持有数量，不可做空 |

### 分批建仓（推荐）

- 首次建仓：计划仓位的 50%
- 确认趋势延续后加仓：剩余 50%
- 加仓条件：股价在首次买入后回踩均线获得支撑并再次上涨

## ═══════════════════════════════════════
## 五、工作流程
## ═══════════════════════════════════════

### 每轮执行步骤：

**Step 1 — 环境感知**（利用已注入的上下文信息）
- 检查市场状态（盘前/盘中/盘后/休市）
- 检查账户余额与可用现金
- 检查当前持仓
- 检查今日和近期订单

**Step 2 — 持仓巡检**（如果有持仓）
- 对每只持仓股票获取实时报价
- 检查是否触发止损线（硬止损 or 均线止损）
- 检查是否触发移动止盈线
- 搜索是否有基本面恶化的重大新闻（财报暴雷、高管抛售等）
- 若触发卖出条件 → 执行卖出
- 若未触发任何条件 → HOLD，输出持仓状态

**Step 3 — 机会扫描**（如果现金充足且大盘环境向好）
- 搜索宏观经济、财报日历、地缘政治等背景信息
- 搜索近期有突破形态的强势股
- 对候选标的进行技术面+基本面双重验证
- 计算盈亏比，确认 ≥ 3:1
- 若满足所有条件 → 按仓位公式计算数量并执行买入
- 若不满足 → 输出"当前无符合条件的买入机会，继续持有现金"

**Step 4 — 输出总结**
- 清晰说明本轮分析逻辑和决策理由
- 列出当前持仓状态和关键监控价位（止损线、止盈线）
- 如无操作，说明原因

## ═══════════════════════════════════════
## 六、工具调用效率要求
## ═══════════════════════════════════════

- **尽量在一次响应中同时调用多个无依赖关系的工具**，减少迭代轮次
- 可以并行调用的工具：
  - get_market_status、get_account_balance、get_positions、get_today_orders
  - 多个不同股票的 get_quote
  - 多个不同主题的搜索工具（宏观经济、财报日历、地缘政治）
- 只有"需要上一步结果才能决定下一步"时，才分开调用
- 中长线策略下，搜索工具应重点用于：确认持仓股基本面是否恶化、验证买入候选标的的景气度逻辑

## ═══════════════════════════════════════
## 七、输出格式要求
## ═══════════════════════════════════════

每次执行后，按以下格式输出总结：

```
【市场环境】简述大盘状态
【持仓巡检】每只持仓股的当前价格、止损线、止盈线、是否触发信号
【操作决策】BUY / SELL / HOLD + 详细理由
【风控计算】如有交易，展示仓位计算过程
【下轮关注】需要重点监控的价位或事件
```

## 当前任务
分析当前市场状况和账户持仓状态。对持仓进行巡检（是否触发止损/止盈），并扫描是否有符合中长线建仓条件的新机会。若无操作信号，返回 HOLD 并说明原因。记住：大部分时间"不操作"就是最好的操作。
"""


class ReActAgent:
    """
    ReAct智能体

    实现思考(Reasoning) -> 行动(Acting) -> 观察(Observation)的循环
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
        """
        初始化ReAct智能体

        Args:
            llm: 大语言模型实例
            tool_registry: 工具注册表
            system_prompt: 系统提示词
            max_iterations: 最大迭代次数
            pre_run_tools: 每次循环开始前预先执行的工具名列表（无需参数，使用默认值）
            feishu_notifier: 飞书通知器（可选），有交易操作时推送消息
            logger: 日志记录器
        """
        self.llm = llm
        self.tool_registry = tool_registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.pre_run_tools = pre_run_tools or []
        self.feishu_notifier = feishu_notifier
        self.logger = logger or logging.getLogger(__name__)

    def _check_and_notify_trading(self, result: str, executed_tools: list[str]) -> None:
        """
        检查是否执行了实际交易操作，如有则发送飞书通知

        Args:
            result: 智能体执行结果
            executed_tools: 本次循环实际执行过的工具名称列表
        """
        if not self.feishu_notifier:
            return
        # 判断是否有实际执行买入或卖出工具
        trading_tools = {"buy_stock", "sell_stock"}
        actual_trading = [t for t in executed_tools if t in trading_tools]
        if actual_trading:
            self.logger.info(f"检测到实际交易操作: {actual_trading}，发送飞书通知")
            self.feishu_notifier.send_trading_alert(result)

    def _build_tools_section(self, available_tools: list[dict]) -> str:
        """
        根据实际传给 LLM 的工具列表，动态生成提示词中的工具能力说明。
        按 search_ 前缀分为搜索工具和交易工具两组。
        """
        trade_lines = []
        search_lines = []
        for t in available_tools:
            name = t["function"]["name"]
            tool = self.tool_registry.get(name)
            desc = tool.description if tool else t["function"].get("description", "")
            if name.startswith("search_"):
                search_lines.append(f"- {name}: {desc}")
            else:
                trade_lines.append(f"- {name}: {desc}")

        parts = []
        if trade_lines:
            parts.append("### 交易工具\n" + "\n".join(trade_lines))
        if search_lines:
            parts.append("### 信息搜索工具（联网搜索，按需使用）\n" + "\n".join(search_lines))
        return "\n\n".join(parts) if parts else "（暂无可用工具）"

    def run(self, user_input: Optional[str] = None) -> str:
        """
        运行智能体

        Args:
            user_input: 用户输入（可选）

        Returns:
            智能体的最终响应
        """
        # 获取工具定义，过滤掉已预执行的工具（它们的结果已注入上下文，无需 LLM 再调用）
        pre_run_set = set(self.pre_run_tools)
        tools = [
            t for t in self.tool_registry.get_openai_tools()
            if t["function"]["name"] not in pre_run_set
        ]

        # 动态渲染系统提示词：根据当前可用工具填充 {tools_section} 占位符
        system_prompt = self.system_prompt.replace(
            "{tools_section}", self._build_tools_section(tools)
        )

        # 初始化消息列表
        messages = [
            ChatMessage(role=Role.SYSTEM, content=system_prompt)
        ]

        # 如果有用户输入，添加到消息列表
        if user_input:
            messages.append(ChatMessage(role=Role.USER, content=user_input))
        else:
            # 默认触发消息
            messages.append(ChatMessage(
                role=Role.USER,
                content="请检查当前市场状态和账户情况，分析是否有合适的交易机会。"
            ))

        self.logger.info("=" * 50)
        self.logger.info("开始ReAct循环")

        # 预执行工具：在第一次 LLM 调用前先执行固定工具列表，结果以文本形式注入上下文
        # 注意：不构造 function_call 消息，避免 Gemini thought_signature 校验失败
        if self.pre_run_tools:
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
                # 将预执行结果作为系统上下文注入，避免构造假的 function_call
                context_msg = "以下是已获取的背景信息（由系统自动收集）：\n\n" + "\n\n".join(pre_results)
                messages.append(ChatMessage(
                    role=Role.USER,
                    content=context_msg,
                ))

        # 记录本次循环实际执行过的工具
        executed_tools: list[str] = []

        # ReAct循环
        for iteration in range(self.max_iterations):
            self.logger.info(f"--- 迭代 {iteration + 1}/{self.max_iterations} ---")

            try:
                # 调用LLM
                response = self.llm.chat(messages, tools=tools, tool_choice="auto")

                # 如果没有工具调用，说明LLM已经完成任务
                if not response.has_tool_calls:
                    self.logger.info("LLM完成任务，无需更多工具调用")
                    self.logger.info(f"最终响应: {response.content}")
                    final_result = response.content or "任务完成，无额外输出"
                    self._check_and_notify_trading(final_result, executed_tools)
                    return final_result

                # 处理工具调用
                # 先将assistant的响应添加到消息列表
                # native_content 保存原生LLM对象（如Gemini的Content），
                # 下一轮转换时直接复用，避免重建时丢失 thought_signature 等内部字段
                assistant_message = ChatMessage(
                    role=Role.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                    native_content=response.native_content,
                )
                messages.append(assistant_message)

                # 执行每个工具调用
                for tool_call in response.tool_calls:
                    self.logger.info(f"调用工具: {tool_call.name}")
                    self.logger.info(f"参数: {json.dumps(tool_call.arguments, ensure_ascii=False)}")

                    # 记录执行过的工具
                    executed_tools.append(tool_call.name)

                    # 执行工具
                    try:
                        result = self.tool_registry.execute(
                            tool_call.name,
                            **tool_call.arguments
                        )
                        self.logger.info(f"工具返回: {result}")
                    except Exception as e:
                        result = json.dumps({"error": str(e)})
                        self.logger.error(f"工具执行错误: {e}")

                    # 将工具结果添加到消息列表
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

        # 达到最大迭代次数
        self.logger.warning(f"达到最大迭代次数 {self.max_iterations}")

        # 最后一次调用LLM，要求总结
        messages.append(ChatMessage(
            role=Role.USER,
            content="已达到最大执行步数，请总结当前状态并给出最终建议。"
        ))

        try:
            final_response = self.llm.chat(messages, tools=None)
            final_result = final_response.content or "达到最大迭代次数，任务中止"
            self._check_and_notify_trading(final_result, executed_tools)
            return final_result
        except Exception as e:
            return f"最终总结出错: {str(e)}"

    def run_once(self, user_input: str) -> str:
        """
        单次运行（不循环，仅一次LLM调用和工具执行）

        Args:
            user_input: 用户输入

        Returns:
            响应结果
        """
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
                            tool_call.name,
                            **tool_call.arguments
                        )
                        results.append(f"{tool_call.name}: {result}")
                    except Exception as e:
                        results.append(f"{tool_call.name}: Error - {str(e)}")

                return "\n".join(results)
            else:
                return response.content or "无响应"

        except Exception as e:
            return f"执行出错: {str(e)}"
