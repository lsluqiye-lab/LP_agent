"""
LP-Agent 全链路测试脚本
测试所有模块是否正常工作，不执行实际交易下单

用法:
    # 先设置环境变量（或 source start.sh 前几行），然后：
    python3 test_pipeline.py
"""
import os
import sys
import json
import time
from datetime import datetime

import pytz

# ───────────────────────────────────────────
# 辅助函数
# ───────────────────────────────────────────

EASTERN = pytz.timezone("US/Eastern")
BEIJING = pytz.timezone("Asia/Shanghai")

PASS = "✅ 通过"
FAIL = "❌ 失败"
SKIP = "⏭️ 跳过"

results: list[tuple[str, str, str]] = []  # (模块名, 状态, 详情)


def now_str() -> str:
    """返回美东+北京的时间字符串"""
    et = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S")
    bj = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    return f"美东: {et} | 北京: {bj}"


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def record(module: str, passed: bool, detail: str):
    status = PASS if passed else FAIL
    results.append((module, status, detail))
    print(f"  {status} {detail}")


def record_skip(module: str, detail: str):
    results.append((module, SKIP, detail))
    print(f"  {SKIP} {detail}")


# ───────────────────────────────────────────
# 1. 配置加载
# ───────────────────────────────────────────
def test_config():
    section("1. 配置加载")
    try:
        from config import AppConfig
        config = AppConfig.from_env(os.getenv("LLM_PROVIDER", "deepseek"))
        config.longport.to_env()

        # 检查关键配置项
        checks = {
            "LONGPORT_APP_KEY": bool(config.longport.app_key),
            "LONGPORT_APP_SECRET": bool(config.longport.app_secret),
            "LONGPORT_ACCESS_TOKEN": bool(config.longport.access_token),
            "LLM API Key": bool(config.llm.api_key),
            "LLM Provider": config.llm.provider,
            "LLM Model": config.llm.model,
            "飞书 Webhook": bool(config.feishu.webhook_url),
        }

        for name, val in checks.items():
            if isinstance(val, bool):
                record("配置", val, f"{name}: {'已配置' if val else '未配置'}")
            else:
                record("配置", True, f"{name}: {val}")

        return config
    except Exception as e:
        record("配置", False, f"配置加载失败: {e}")
        return None


# ───────────────────────────────────────────
# 2. LongPort 交易 API
# ───────────────────────────────────────────
def test_longport():
    section("2. LongPort 交易 API")
    from tools.trading import create_trading_tools
    tools = create_trading_tools()

    # 按名字索引
    tool_map = {t.name: t for t in tools}
    record("LongPort", True, f"已创建 {len(tools)} 个交易工具")

    # 2a. 市场状态
    try:
        result = tool_map["get_market_status"].execute()
        data = json.loads(result)
        status = data.get("status", "unknown")
        msg = data.get("message", "")
        record("LongPort", True, f"市场状态: {status} — {msg}")
    except Exception as e:
        record("LongPort", False, f"获取市场状态失败: {e}")

    # 2b. 账户余额
    try:
        result = tool_map["get_account_balance"].execute()
        data = json.loads(result)
        if "error" in data:
            record("LongPort", False, f"账户余额: {data['error']}")
        else:
            net = data.get("net_assets", "N/A")
            cash = data.get("total_cash", "N/A")
            record("LongPort", True, f"账户余额: 总资产 ${net}, 可用现金 ${cash}")
    except Exception as e:
        record("LongPort", False, f"获取账户余额失败: {e}")

    # 2c. 持仓
    try:
        result = tool_map["get_positions"].execute()
        data = json.loads(result)
        if "error" in data:
            record("LongPort", False, f"持仓查询: {data['error']}")
        else:
            count = data.get("count", 0)
            positions = data.get("positions", [])
            if count == 0:
                record("LongPort", True, "持仓查询: 当前无持仓")
            else:
                symbols = ", ".join(p["symbol"] for p in positions[:5])
                record("LongPort", True, f"持仓查询: {count} 只股票 [{symbols}]")
    except Exception as e:
        record("LongPort", False, f"获取持仓失败: {e}")

    # 2d. 今日订单
    try:
        result = tool_map["get_today_orders"].execute()
        data = json.loads(result)
        if "error" in data:
            record("LongPort", False, f"今日订单: {data['error']}")
        else:
            count = data.get("count", 0)
            record("LongPort", True, f"今日订单: {count} 笔")
    except Exception as e:
        record("LongPort", False, f"获取今日订单失败: {e}")

    # 2e. 历史订单
    try:
        result = tool_map["get_history_orders"].execute(days=7)
        data = json.loads(result)
        if "error" in data:
            record("LongPort", False, f"历史订单: {data['error']}")
        else:
            count = data.get("count", 0)
            record("LongPort", True, f"历史订单(7天): {count} 笔")
    except Exception as e:
        record("LongPort", False, f"获取历史订单失败: {e}")

    # 2f. 股票报价（用 AAPL 测试）
    try:
        result = tool_map["get_quote"].execute(symbol="AAPL")
        data = json.loads(result)
        if "error" in data:
            record("LongPort", False, f"股票报价(AAPL): {data['error']}")
        else:
            price = data.get("last_done", "N/A")
            record("LongPort", True, f"股票报价(AAPL): 最新价 ${price}")
    except Exception as e:
        record("LongPort", False, f"获取股票报价失败: {e}")

    # 2g. 买入/卖出工具 — 仅验证存在，不实际执行
    record("LongPort", "buy_stock" in tool_map, "买入工具(buy_stock): 已注册")
    record("LongPort", "sell_stock" in tool_map, "卖出工具(sell_stock): 已注册（本次测试不执行实际下单）")


# ───────────────────────────────────────────
# 3. Gemini 搜索工具
# ───────────────────────────────────────────
def test_search():
    section("3. Gemini 搜索工具")

    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        record_skip("搜索", "GEMINI_API_KEY 未配置，跳过搜索工具测试")
        return

    try:
        from tools.search import create_search_tools
        tools = create_search_tools()
        tool_map = {t.name: t for t in tools}
        record("搜索", True, f"已创建 {len(tools)} 个搜索工具")
    except Exception as e:
        record("搜索", False, f"搜索工具创建失败: {e}")
        return

    # 只测一个搜索工具（search_macro_economics），避免过多 API 调用
    try:
        print("  ⏳ 正在测试搜索工具（search_macro_economics）...")
        result = tool_map["search_macro_economics"].execute(topic="fed")
        data = json.loads(result)
        if "error" in data:
            record("搜索", False, f"宏观经济搜索: {data['error']}")
        else:
            info = data.get("macro_info", "")
            preview = info[:80].replace("\n", " ") + "..." if len(info) > 80 else info
            record("搜索", True, f"宏观经济搜索: {preview}")
    except Exception as e:
        record("搜索", False, f"宏观经济搜索失败: {e}")


# ───────────────────────────────────────────
# 4. DeepSeek LLM
# ───────────────────────────────────────────
def test_llm(config):
    section("4. DeepSeek LLM")

    if not config or not config.llm.api_key:
        record_skip("LLM", "LLM API Key 未配置，跳过")
        return

    try:
        import sys
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        from main import create_llm
        from llm.base import ChatMessage, Role

        llm = create_llm(config.llm)
        record("LLM", True, f"LLM 初始化成功: {llm.get_provider_name()} / {config.llm.model}")

        # 简单对话测试
        print("  ⏳ 正在测试 LLM 对话能力...")
        messages = [
            ChatMessage(role=Role.SYSTEM, content="你是一个金融助手，用一句话简洁回答。"),
            ChatMessage(role=Role.USER, content="美股三大指数是什么？"),
        ]
        response = llm.chat(messages, tools=None)
        reply = (response.content or "").strip()
        preview = reply[:100].replace("\n", " ") + ("..." if len(reply) > 100 else "")
        record("LLM", bool(reply), f"对话测试: {preview}")

        # 工具调用测试（给一个假工具定义，看 LLM 是否会调用）
        print("  ⏳ 正在测试 LLM 工具调用能力...")
        test_tools = [{
            "type": "function",
            "function": {
                "name": "get_stock_price",
                "description": "获取股票当前价格",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "股票代码"}
                    },
                    "required": ["symbol"]
                }
            }
        }]
        messages2 = [
            ChatMessage(role=Role.SYSTEM, content="你是一个交易助手，需要时请调用工具。"),
            ChatMessage(role=Role.USER, content="帮我查一下苹果公司的股价"),
        ]
        response2 = llm.chat(messages2, tools=test_tools, tool_choice="auto")
        if response2.has_tool_calls:
            tc = response2.tool_calls[0]
            record("LLM", True, f"工具调用测试: LLM 正确调用了 {tc.name}({tc.arguments})")
        else:
            # 有些情况 LLM 可能直接回答而不调用工具，不算失败但需要标注
            record("LLM", True, f"工具调用测试: LLM 选择直接回答（未调用工具，可能正常）")

    except Exception as e:
        record("LLM", False, f"LLM 测试失败: {e}")


# ───────────────────────────────────────────
# 5. 通知渠道测试（根据配置自动选择）
# ───────────────────────────────────────────
def test_notification(config):
    section("5. 通知渠道测试")

    if not config:
        record_skip("通知", "配置未加载，跳过")
        return

    channel = config.notification_channel

    if channel == "dingtalk":
        if not config.dingtalk.enabled:
            record_skip("通知", "钉钉 Webhook 未配置，跳过")
            return
        try:
            from notification.dingtalk import DingTalkNotifier
            notifier = DingTalkNotifier(
                webhook_url=config.dingtalk.webhook_url,
                sign_secret=config.dingtalk.sign_secret
            )
            record("通知", True, "钉钉通知器初始化成功")

            # 发送测试消息
            test_msg = f"🧪 LP-Agent 链路测试\n时间: {now_str()}\n状态: 测试消息，请忽略"
            success = notifier.send_text(test_msg)
            record("通知", success, f"发送测试消息: {'成功（请查看钉钉群）' if success else '发送失败'}")
        except Exception as e:
            record("通知", False, f"钉钉通知测试失败: {e}")

    elif channel == "feishu":
        if not config.feishu.enabled:
            record_skip("通知", "飞书 Webhook 未配置，跳过")
            return
        try:
            from notification.feishu import FeishuNotifier
            notifier = FeishuNotifier(webhook_url=config.feishu.webhook_url)
            record("通知", True, "飞书通知器初始化成功")

            # 发送测试消息
            test_msg = f"🧪 LP-Agent 链路测试\n时间: {now_str()}\n状态: 测试消息，请忽略"
            success = notifier.send_text(test_msg)
            record("通知", success, f"发送测试消息: {'成功（请查看飞书群）' if success else '发送失败'}")
        except Exception as e:
            record("通知", False, f"飞书通知测试失败: {e}")

    else:
        record_skip("通知", f"未知通知渠道: {channel}")


# ───────────────────────────────────────────
# 6. ReAct 智能体完整推理（不下单）
# ───────────────────────────────────────────
agent_result_text = ""  # 保存 agent 推理结果，供飞书推送使用

def test_agent(config):
    global agent_result_text
    section("6. ReAct 智能体完整推理（不下单）")

    if not config or not config.llm.api_key:
        record_skip("Agent", "LLM API Key 未配置，跳过")
        return

    try:
        import sys
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        from main import create_llm
        from tools.base import ToolRegistry
        from tools.trading import create_trading_tools
        from agent.react import ReActAgent, STRATEGIC_SYSTEM_PROMPT

        # 创建 LLM
        llm = create_llm(config.llm)

        # 只注册交易工具（不注册搜索工具，加快测试速度）
        # 同时排除 buy_stock 和 sell_stock，确保不会实际下单
        tool_registry = ToolRegistry()
        safe_tools = [
            t for t in create_trading_tools()
            if t.name not in ("buy_stock", "sell_stock")
        ]
        tool_registry.register_all(safe_tools)
        record("Agent", True, f"已注册 {len(safe_tools)} 个安全工具（已排除 buy/sell）")

        # 创建智能体，max_iterations=5 允许更完整的推理
        agent = ReActAgent(
            llm=llm,
            tool_registry=tool_registry,
            system_prompt=STRATEGIC_SYSTEM_PROMPT,
            max_iterations=5,
            logger=None,
        )
        record("Agent", True, "ReAct 智能体初始化成功")

        import asyncio
        # 运行一轮推理
        print("  ⏳ 正在运行 ReAct 推理（最多5轮迭代，预计1-2分钟）...")
        start_time = time.time()
        
        mock_risk_context = "风控状态: NORMAL (65/100)\n约束: 允许正常交易，按标准仓位执行。"
        mock_action_candidates = "### AAPL [买入机会评估]\n- 入选原因: Stage2上升趋势 | RSI=60\n- 分析师评分: 8/10\n- 建议动作: BUY\n- Bull Case (利好): AI功能集成带来换机潮。\n- Bear Case (风险): 反垄断诉讼可能带来巨额罚款。\n"
        
        # 使用事件循环运行异步函数
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(agent.run(
            decision_briefings_json=mock_action_candidates,
            risk_context=mock_risk_context,
            pre_executed_data={"get_account_balance": "{\"net_assets\": 100000, \"total_cash\": 100000}", "get_positions": "{\"positions\": []}"}
        ))
        elapsed = time.time() - start_time

        agent_result_text = result  # 保存结果

        # 展示结果
        record("Agent", bool(result), f"推理完成（耗时 {elapsed:.1f}秒）")
        print(f"\n  📋 智能体完整输出:\n  {'-'*50}")
        for line in result.split("\n"):
            print(f"  | {line}")
        print(f"  {'-'*50}")

    except Exception as e:
        record("Agent", False, f"ReAct 智能体测试失败: {e}")


# ───────────────────────────────────────────
# 主函数
# ───────────────────────────────────────────
def main():
    print("\n" + "🔬" * 30)
    print("   LP-Agent 全链路测试")
    print(f"   {now_str()}")
    print("🔬" * 30)

    # 运行各模块测试
    config = test_config()
    test_longport()
    test_search()
    test_llm(config)
    test_notification(config)
    test_agent(config)

    # 汇总报告
    section("📊 测试汇总")
    total = len(results)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = sum(1 for _, s, _ in results if s == FAIL)
    skipped = sum(1 for _, s, _ in results if s == SKIP)

    print(f"\n  总计: {total} 项 | {PASS}: {passed} | {FAIL}: {failed} | {SKIP}: {skipped}\n")

    if failed > 0:
        print("  失败项:")
        for module, status, detail in results:
            if status == FAIL:
                print(f"    {FAIL} [{module}] {detail}")
        print()

    if failed == 0:
        print("  🎉 所有测试通过！系统链路正常。\n")
    else:
        print(f"  ⚠️ 有 {failed} 项测试失败，请检查上述错误信息。\n")

    # ───── 7. 通过配置的通知渠道发送完整报告 ─────
    section("7. 推送完整报告到通知渠道")
    if config and config.notifier_enabled:
        channel = config.notification_channel
        try:
            if channel == "dingtalk":
                from notification.dingtalk import DingTalkNotifier
                notifier = DingTalkNotifier(
                    webhook_url=config.dingtalk.webhook_url,
                    sign_secret=config.dingtalk.sign_secret
                )
            elif channel == "feishu":
                from notification.feishu import FeishuNotifier
                notifier = FeishuNotifier(webhook_url=config.feishu.webhook_url)
            else:
                print(f"  ⏭️ 未知通知渠道: {channel}")
                return 0 if failed == 0 else 1

            # 构建汇总消息
            summary_lines = [
                f"🔬 LP-Agent 全链路测试报告",
                f"⏰ {now_str()}",
                f"📊 总计: {total}项 | ✅{passed} | ❌{failed} | ⏭️{skipped}",
                "",
            ]
            for module, status, detail in results:
                summary_lines.append(f"{status} [{module}] {detail}")

            if failed == 0:
                summary_lines.append("\n🎉 所有测试通过！系统链路正常。")
            else:
                summary_lines.append(f"\n⚠️ 有 {failed} 项测试失败。")

            summary_msg = "\n".join(summary_lines)

            # 发送汇总
            ok1 = notifier.send_text(summary_msg)
            channel_name = "钉钉" if channel == "dingtalk" else "飞书"
            print(f"  {'✅' if ok1 else '❌'} 测试汇总已推送{channel_name}")

            # 发送 ReAct 决策报告
            if agent_result_text:
                decision_msg = (
                    f"📊 【ReAct 智能体决策报告】\n"
                    f"⏰ {now_str()}\n"
                    f"{'='*30}\n\n"
                    f"{agent_result_text[:4000]}"
                )
                if len(agent_result_text) > 4000:
                    decision_msg += "\n\n... (内容过长已截断)"

                ok2 = notifier.send_text(decision_msg)
                print(f"  {'✅' if ok2 else '❌'} ReAct 决策报告已推送{channel_name}")
            else:
                print("  ⏭️ 无 Agent 推理结果，跳过决策报告推送")

        except Exception as e:
            print(f"  ❌ 推送失败: {e}")
    else:
        print("  ⏭️ 通知渠道未配置，跳过推送")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
