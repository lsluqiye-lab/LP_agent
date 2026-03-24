"""
LP-Agent v2.0 主入口
AI自动交易智能体 - 分阶段执行架构

执行流程:
  Phase 1: 数据收集 (预执行工具，收集背景数据)
  Phase 2: 风控评分 (MacroRiskManager 计算0-100分)
  Phase 3: ReAct推理 (Bear-Case-First 决策)
  Phase 4: 执行交易 (在ReAct循环内完成)
  Phase 5: 收盘复盘 (16:30 EST 自动触发)
"""
import signal
import sys
import time
import json
import logging
from datetime import datetime, time as dt_time

import pytz
import holidays

from config import AppConfig, WATCHLIST
from logger import setup_logger
from llm.deepseek import DeepSeekLLM
from llm.gemini import GeminiLLM
from tools.base import ToolRegistry
from tools.trading import create_trading_tools
from tools.search import create_search_tools
from tools.market_data import create_market_data_tools
from agent.react import ReActAgent, TRADING_SYSTEM_PROMPT
from agent.risk_manager import get_risk_manager
from agent.review import ReviewAgent
from data.trade_logger import get_trade_logger
from data.memory import get_trading_memory
from notification.feishu import FeishuNotifier


def is_trading_hours(eastern_time: datetime) -> bool:
    """
    检查是否在交易时段（盘前1小时 + 盘中 + 盘后1小时）
    8:30 - 17:00 ET
    """
    nyse_holidays = holidays.NYSE()
    today_str = eastern_time.strftime('%Y-%m-%d')
    if nyse_holidays.get(today_str):
        return False
    if eastern_time.weekday() >= 5:
        return False
    current_t = eastern_time.time()
    if dt_time(8, 30) <= current_t <= dt_time(17, 0):
        return True
    return False


def is_review_time(eastern_time: datetime, config: AppConfig) -> bool:
    """检查是否到了复盘时间（16:30 ET）"""
    current_minutes = eastern_time.hour * 60 + eastern_time.minute
    trigger_minutes = config.review.trigger_time_hour * 60 + config.review.trigger_time_minute
    return current_minutes >= trigger_minutes


def get_sleep_interval(config: AppConfig, eastern_time: datetime) -> int:
    """获取休眠间隔"""
    if is_trading_hours(eastern_time):
        return config.agent.sleep_interval_trading
    return config.agent.sleep_interval_non_trading


def create_llm(config: AppConfig):
    """根据配置创建LLM实例"""
    provider = config.llm.provider
    if provider == "deepseek":
        return DeepSeekLLM(
            api_key=config.llm.api_key,
            base_url=config.llm.base_url,
            model=config.llm.model,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
    elif provider == "gemini":
        return GeminiLLM(
            api_key=config.llm.api_key,
            model=config.llm.model,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
    else:
        raise ValueError(f"不支持的LLM提供商: {provider}")


# ═══════════════════════════════════════════
# Phase 1: 数据收集
# ═══════════════════════════════════════════

def phase1_collect_data(tool_registry: ToolRegistry, logger: logging.Logger) -> dict:
    """
    Phase 1: 收集背景数据

    预执行一组工具，将结果收集到 dict 中，
    既作为 RiskManager 的输入，也作为 ReAct 的上下文注入。
    """
    logger.info("[Phase 1] 开始数据收集")
    data = {}

    # 1. 账户与订单数据
    account_tools = [
        "get_market_status",
        "get_positions",
        "get_account_balance",
        "get_today_orders",
    ]
    for tool_name in account_tools:
        tool = tool_registry.get(tool_name)
        if tool is None:
            logger.warning(f"工具 {tool_name} 未注册")
            continue
        try:
            result = tool_registry.execute(tool_name)
            data[tool_name] = result
            logger.info(f"  {tool_name}: OK")
        except Exception as e:
            data[tool_name] = json.dumps({"error": str(e)})
            logger.error(f"  {tool_name}: {e}")

    # 2. 大盘环境 + 市场温度（风控评分的核心输入）
    try:
        data["get_market_overview"] = tool_registry.execute("get_market_overview")
        logger.info("  get_market_overview: OK")
    except Exception as e:
        data["get_market_overview"] = json.dumps({"error": str(e)})
        logger.error(f"  get_market_overview: {e}")

    # 3. 标的池快速扫描
    try:
        data["scan_watchlist"] = tool_registry.execute("scan_watchlist")
        logger.info("  scan_watchlist: OK")
    except Exception as e:
        data["scan_watchlist"] = json.dumps({"error": str(e)})
        logger.error(f"  scan_watchlist: {e}")

    # 4. 搜索工具（宏观、地缘、财报日历）- 可能失败但不影响核心流程
    search_tools = [
        "search_macro_economics",
        "search_earnings_calendar",
        "search_geopolitical_news",
    ]
    for tool_name in search_tools:
        tool = tool_registry.get(tool_name)
        if tool is None:
            continue
        try:
            result = tool_registry.execute(tool_name)
            data[tool_name] = result
            logger.info(f"  {tool_name}: OK")
        except Exception as e:
            data[tool_name] = json.dumps({"error": str(e)})
            logger.warning(f"  {tool_name}: {e} (搜索工具失败不影响核心流程)")

    logger.info(f"[Phase 1] 数据收集完成，共 {len(data)} 项")
    return data


# ═══════════════════════════════════════════
# Phase 2: 风控评分
# ═══════════════════════════════════════════

def phase2_risk_scoring(collected_data: dict, config: AppConfig, logger: logging.Logger) -> dict:
    """
    Phase 2: 宏观风控评分

    解析 Phase 1 收集的数据，计算综合风控评分。
    """
    logger.info("[Phase 2] 开始风控评分")

    risk_manager = get_risk_manager(config.risk)

    # 构建 RiskManager 需要的数据结构
    risk_input = {}

    # 解析 market_overview
    market_raw = collected_data.get("get_market_overview", "{}")
    try:
        market_data = json.loads(market_raw) if isinstance(market_raw, str) else market_raw
        risk_input["indexes"] = market_data.get("indexes", {})
        risk_input["market_temperature"] = market_data.get("market_temperature", {})
        risk_input["market_verdict"] = market_data.get("market_verdict", "")
    except Exception:
        risk_input["indexes"] = {}
        risk_input["market_temperature"] = {}

    # 解析 watchlist scan
    scan_raw = collected_data.get("scan_watchlist", "{}")
    try:
        scan_data = json.loads(scan_raw) if isinstance(scan_raw, str) else scan_raw
        risk_input["watchlist_scan"] = scan_data.get("watchlist_scan", [])
    except Exception:
        risk_input["watchlist_scan"] = []

    # 计算评分
    risk_result = risk_manager.calculate_risk_score(risk_input)

    logger.info(f"[Phase 2] 风控评分: {risk_result['score']}/100 ({risk_result['regime']})")
    logger.info(f"  约束: {risk_result['constraints']['message']}")

    return risk_result


# ═══════════════════════════════════════════
# Phase 2.5: 提取行动候选清单
# ═══════════════════════════════════════════

def phase2_5_extract_candidates(
    collected_data: dict,
    risk_result: dict,
    logger: logging.Logger,
) -> str:
    """
    Phase 2.5: 从 scan_watchlist 结果中提取行动候选清单

    将有信号的标的 + 所有持仓标的组织成结构化文本，
    注入 ReAct 提示词强制 LLM 逐一分析。
    """
    if logger is None:
        logger = logging.getLogger(__name__)
    logger.info("[Phase 2.5] 提取行动候选清单")

    candidates = []
    regime = risk_result.get("regime", "normal")
    allow_buy = risk_result.get("constraints", {}).get("allow_new_buy", False)

    # 1. 从持仓中提取（无论风控状态，持仓都必须巡检）
    positions_raw = collected_data.get("get_positions", "{}")
    try:
        pos_data = json.loads(positions_raw) if isinstance(positions_raw, str) else positions_raw
        for pos in pos_data.get("positions", []):
            sym = pos.get("symbol", "")
            if sym in WATCHLIST:
                candidates.append({
                    "symbol": sym,
                    "reason": f"当前持仓 {pos.get('quantity', '?')}股, 成本${pos.get('cost_price', '?')}",
                    "action_type": "持仓巡检（检查止损/止盈）",
                })
    except Exception:
        pass

    # 2. 从 scan_watchlist 中提取有信号的标的（仅当允许买入时）
    if allow_buy:
        scan_raw = collected_data.get("scan_watchlist", "{}")
        try:
            scan_data = json.loads(scan_raw) if isinstance(scan_raw, str) else scan_raw
            held_symbols = {c["symbol"] for c in candidates}

            for item in scan_data.get("watchlist_scan", []):
                sym = item.get("symbol", "")
                if sym in held_symbols:
                    continue  # 已在持仓巡检中

                flags = item.get("flags", [])
                stage2 = item.get("stage2", False)
                rsi = item.get("rsi_14")
                ret = item.get("return_20d_pct")

                # 选入条件：Stage2 或 有技术信号 或 近期涨幅较好
                should_include = (
                    stage2
                    or len(flags) > 0
                    or (ret is not None and ret > 3)
                    or (rsi is not None and 40 <= rsi <= 70)
                )

                if should_include:
                    reason_parts = []
                    if stage2:
                        reason_parts.append("Stage2上升趋势")
                    if flags:
                        reason_parts.append(f"信号: {', '.join(flags)}")
                    if ret is not None:
                        reason_parts.append(f"20日涨幅{ret:+.1f}%")
                    if rsi is not None:
                        reason_parts.append(f"RSI={rsi:.0f}")

                    candidates.append({
                        "symbol": sym,
                        "reason": " | ".join(reason_parts) if reason_parts else "标的池成员",
                        "action_type": "买入机会评估",
                    })
        except Exception:
            pass

    # 3. 如果允许买入但没有任何候选，强制加入 signal_summary.score 最高的前3只
    if allow_buy and not any(c["action_type"] == "买入机会评估" for c in candidates):
        try:
            scan_data = json.loads(collected_data.get("scan_watchlist", "{}"))
            held_symbols = {c["symbol"] for c in candidates}
            scan_items = [
                item for item in scan_data.get("watchlist_scan", [])
                if item.get("symbol") not in held_symbols and item.get("stage2")
            ]
            # 按 return_20d_pct 排序
            scan_items.sort(key=lambda x: x.get("return_20d_pct", -999), reverse=True)
            for item in scan_items[:3]:
                candidates.append({
                    "symbol": item["symbol"],
                    "reason": f"Stage2 | 20日涨幅{item.get('return_20d_pct', 0):+.1f}% (强制候选)",
                    "action_type": "买入机会评估",
                })
        except Exception:
            pass

    # 4. 格式化输出
    if not candidates:
        text = "当前无候选标的：无持仓且风控不允许买入，或标的池全部处于弱势。本轮仅输出市场观察总结。"
    else:
        lines = [f"共 {len(candidates)} 只候选标的，你必须逐一分析：\n"]
        for i, c in enumerate(candidates, 1):
            lines.append(f"{i}. **{c['symbol']}** [{c['action_type']}]")
            lines.append(f"   原因: {c['reason']}")
        text = "\n".join(lines)

    logger.info(f"[Phase 2.5] 候选清单: {len(candidates)} 只")
    for c in candidates:
        logger.info(f"  {c['symbol']}: {c['action_type']} - {c['reason']}")

    return text


def phase3_react_reasoning(
    agent: ReActAgent,
    collected_data: dict,
    risk_result: dict,
    action_candidates: str,
    logger: logging.Logger,
) -> str:
    """
    Phase 3&4: ReAct 推理 + 执行

    将 Phase 1 的数据、Phase 2 的风控结果、Phase 2.5 的候选清单注入 ReAct 循环。
    """
    if logger is None:
        logger = logging.getLogger(__name__)
    logger.info("[Phase 3] 开始 ReAct 推理")

    risk_manager = get_risk_manager()
    risk_context = risk_manager.get_risk_summary()

    result = agent.run(
        risk_context=risk_context,
        pre_executed_data=collected_data,
        action_candidates=action_candidates,
    )

    logger.info("[Phase 3] ReAct 推理完成")
    return result


# ═══════════════════════════════════════════
# Phase 5: 收盘复盘
# ═══════════════════════════════════════════

def phase5_daily_review(review_agent: ReviewAgent, logger: logging.Logger) -> str:
    """
    Phase 5: 每日复盘

    仅在收盘后触发一次。
    """
    logger.info("[Phase 5] 开始每日复盘")
    report = review_agent.run()
    logger.info("[Phase 5] 复盘完成")
    return report


# ═══════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════

def main():
    """主函数"""
    # 信号处理
    def _graceful_shutdown(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGHUP, _graceful_shutdown)

    # 加载配置
    import os
    llm_provider = os.getenv("LLM_PROVIDER", "deepseek")
    config = AppConfig.from_env(llm_provider)
    config.longport.to_env()

    # 设置日志
    logger = setup_logger("trading_agent", config.log)
    logger.info("=" * 60)
    logger.info("LP-Agent v2.0 启动")
    logger.info(f"LLM: {config.llm.provider} / {config.llm.model}")
    logger.info(f"标的池: {', '.join(WATCHLIST)}")
    logger.info(f"交易间隔: {config.agent.sleep_interval_trading}s")
    logger.info(f"风控阈值: lockdown<{config.risk.score_lockdown} | cautious<{config.risk.score_cautious} | normal<{config.risk.score_normal}")
    logger.info(f"复盘时间: {config.review.trigger_time_hour}:{config.review.trigger_time_minute:02d} ET")
    logger.info("=" * 60)

    # 验证配置
    if not config.llm.api_key:
        logger.error(f"未配置 {config.llm.provider.upper()}_API_KEY")
        sys.exit(1)

    # 创建 LLM
    try:
        llm = create_llm(config)
        logger.info(f"LLM初始化成功: {llm.get_provider_name()}")
    except Exception as e:
        logger.error(f"LLM初始化失败: {e}")
        sys.exit(1)

    # 创建工具注册表
    tool_registry = ToolRegistry()

    trading_tools = create_trading_tools()
    tool_registry.register_all(trading_tools)
    logger.info(f"已注册 {len(trading_tools)} 个交易工具")

    try:
        market_data_tools = create_market_data_tools()
        tool_registry.register_all(market_data_tools)
        logger.info(f"已注册 {len(market_data_tools)} 个行情数据工具")
    except Exception as e:
        logger.warning(f"行情数据工具初始化失败: {e}")

    try:
        search_tools = create_search_tools()
        tool_registry.register_all(search_tools)
        logger.info(f"已注册 {len(search_tools)} 个搜索工具")
    except Exception as e:
        logger.warning(f"搜索工具初始化失败: {e}")

    logger.info(f"工具总数: {len(tool_registry)}")

    # 飞书通知器
    feishu_notifier = None
    if config.feishu.enabled:
        try:
            feishu_notifier = FeishuNotifier(webhook_url=config.feishu.webhook_url)
            logger.info("飞书通知器初始化成功")
        except Exception as e:
            logger.warning(f"飞书通知器初始化失败: {e}")

    # 预执行工具列表（这些工具在 Phase 1 中执行，不在 ReAct 中重复）
    pre_run_tool_names = [
        "get_market_status",
        "get_positions",
        "get_account_balance",
        "get_today_orders",
        "get_market_overview",
        "scan_watchlist",
        "search_macro_economics",
        "search_earnings_calendar",
        "search_geopolitical_news",
    ]

    # 创建交易记忆模块
    trading_memory = get_trading_memory()
    logger.info(f"交易记忆初始化完成: {trading_memory.get_rules_count()} 条规则, {trading_memory.get_summaries_count()} 天摘要")

    # 创建 ReAct Agent
    agent = ReActAgent(
        llm=llm,
        tool_registry=tool_registry,
        system_prompt=TRADING_SYSTEM_PROMPT,
        max_iterations=config.agent.max_iterations,
        pre_run_tools=pre_run_tool_names,  # 标记这些工具已预执行
        feishu_notifier=feishu_notifier,
        trading_memory=trading_memory,
        logger=logger,
    )
    logger.info("ReAct Agent v2.0 初始化完成")

    # 创建 Review Agent
    review_agent = ReviewAgent(
        llm=llm,
        config=config.review,
        feishu_notifier=feishu_notifier,
        trading_memory=trading_memory,
        logger_instance=logger,
    )
    logger.info("Review Agent 初始化完成")

    # 交易日志
    trade_logger = get_trade_logger()

    # ── 主循环 ──
    eastern = pytz.timezone('US/Eastern')
    beijing = pytz.timezone('Asia/Shanghai')
    last_date = None  # 用于检测日期变更，重置复盘标志

    while True:
        try:
            current_time = datetime.now(eastern)
            beijing_time = datetime.now(beijing)
            current_date = current_time.strftime('%Y-%m-%d')

            # 日期变更：重置复盘标志
            if current_date != last_date:
                review_agent.reset_daily_flag()
                last_date = current_date
                logger.info(f"新交易日: {current_date}")

            logger.info("=" * 60)
            logger.info(
                f"新一轮 | 美东: {current_time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"| 北京: {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # 检查是否在交易时段
            if not is_trading_hours(current_time):
                # ── Phase 5: 检查是否需要复盘 ──
                if review_agent.should_run():
                    try:
                        review_report = phase5_daily_review(review_agent, logger)
                        logger.info(f"复盘报告:\n{review_report}")
                    except Exception as e:
                        logger.error(f"复盘执行出错: {e}", exc_info=True)
                        trade_logger.log_error("daily_review", str(e))
                else:
                    logger.info("非交易时段，跳过")
            else:
                # ── Phase 1: 数据收集 ──
                collected_data = phase1_collect_data(tool_registry, logger)

                # ── Phase 2: 风控评分 ──
                risk_result = phase2_risk_scoring(collected_data, config, logger)

                # ── Phase 2.5: 提取行动候选清单 ──
                action_candidates = phase2_5_extract_candidates(collected_data, risk_result, logger)

                # ── Phase 3 & 4: ReAct 推理 + 执行 ──
                result = phase3_react_reasoning(agent, collected_data, risk_result, action_candidates, logger)
                logger.info(f"本轮结果:\n{result}")

            # 休眠
            sleep_seconds = get_sleep_interval(config, current_time)
            next_time = datetime.now(beijing)
            logger.info(
                f"休眠 {sleep_seconds}s | "
                f"下次约 北京 {next_time.strftime('%H:%M:%S')} 之后"
            )
            logger.info("=" * 60)

            time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            logger.info("收到中断信号，正在退出...")
            break
        except Exception as e:
            logger.error(f"主循环出错: {e}", exc_info=True)
            try:
                trade_logger.log_error("main_loop", str(e))
            except Exception:
                pass
            time.sleep(60)

    logger.info("LP-Agent v2.0 已退出")


if __name__ == "__main__":
    main()
