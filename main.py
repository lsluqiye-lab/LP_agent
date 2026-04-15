"""
LP-Agent v3.0 主入口 (Strategic Multi-Agent)
AI自动交易智能体 - 专家协作架构

执行流程:
  Phase 1: 数据收集 (预执行工具)
  Phase 2: 风控评分 (MacroRiskManager)
  Phase 2.5: 提取候选 (信号扫描)
  Phase 3: Map 专家研报 (ExpertOrchestrator 并发多专家分析)
  Phase 4: Reduce 战略决策 (ReActAgent CIO 决策)
  Phase 5: 收盘复盘 (16:30 EST 自动触发)
"""
import signal
import sys
import time
import json
import logging
import asyncio
import concurrent.futures
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
from agent.react import ReActAgent, STRATEGIC_SYSTEM_PROMPT
from agent.orchestrator import ExpertOrchestrator
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
    if dt_time(9, 30) <= current_t <= dt_time(16, 0):
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


def create_llm(llm_config: "LLMConfig"):
    """根据指定的LLM配置创建LLM实例"""
    if not llm_config:
        raise ValueError("LLM配置不能为空")
        
    provider = llm_config.provider
    if provider == "deepseek":
        return DeepSeekLLM(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
            model=llm_config.model,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
        )
    elif provider == "gemini":
        return GeminiLLM(
            api_key=llm_config.api_key,
            model=llm_config.model,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
        )
    else:
        raise ValueError(f"不支持的LLM提供商: {provider}")


# ═══════════════════════════════════════════
# Phase 1: 数据收集
# ═══════════════════════════════════════════

def phase1_collect_data(tool_registry: ToolRegistry, logger: logging.Logger) -> dict:
    """
    Phase 1: 收集背景数据
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
        try:
            result = tool_registry.execute(tool_name)
            data[tool_name] = result
        except Exception as e:
            data[tool_name] = json.dumps({"error": str(e)})

    # 2. 大盘环境 + 市场温度
    try:
        data["get_market_overview"] = tool_registry.execute("get_market_overview")
    except Exception as e:
        data["get_market_overview"] = json.dumps({"error": str(e)})

    # 3. 标的池快速扫描
    try:
        data["scan_watchlist"] = tool_registry.execute("scan_watchlist")
    except Exception as e:
        data["scan_watchlist"] = json.dumps({"error": str(e)})

    logger.info(f"[Phase 1] 数据收集完成，共 {len(data)} 项")
    return data


# ═══════════════════════════════════════════
# Phase 2: 风控评分
# ═══════════════════════════════════════════

def phase2_risk_scoring(collected_data: dict, config: AppConfig, logger: logging.Logger) -> dict:
    """
    Phase 2: 宏观风控评分
    """
    logger.info("[Phase 2] 开始风控评分")
    risk_manager = get_risk_manager(config.risk)
    
    risk_input = {}
    market_raw = collected_data.get("get_market_overview", "{}")
    try:
        market_data = json.loads(market_raw) if isinstance(market_raw, str) else market_raw
        risk_input["indexes"] = market_data.get("indexes", {})
        risk_input["market_temperature"] = market_data.get("market_temperature", {})
    except Exception:
        pass

    scan_raw = collected_data.get("scan_watchlist", "{}")
    try:
        scan_data = json.loads(scan_raw) if isinstance(scan_raw, str) else scan_raw
        risk_input["watchlist_scan"] = scan_data.get("watchlist_scan", [])
    except Exception:
        pass

    risk_result = risk_manager.calculate_risk_score(risk_input)
    logger.info(f"[Phase 2] 风控评分: {risk_result['score']}/100 ({risk_result['regime']})")
    return risk_result


# ═══════════════════════════════════════════
# Phase 2.5: 提取候选
# ═══════════════════════════════════════════

def phase2_5_extract_candidates(collected_data: dict, risk_result: dict, logger: logging.Logger) -> list:
    """
    Phase 2.5: 提取需要专家分析的候选标的
    """
    logger.info("[Phase 2.5] 提取行动候选清单")
    candidates = []
    allow_buy = risk_result.get("constraints", {}).get("allow_new_buy", False)

    # 1. 持仓必选
    positions_raw = collected_data.get("get_positions", "{}")
    try:
        pos_data = json.loads(positions_raw) if isinstance(positions_raw, str) else positions_raw
        for pos in pos_data.get("positions", []):
            sym = pos.get("symbol", "")
            if sym in WATCHLIST:
                candidates.append({"symbol": sym, "type": "POSITION"})
    except Exception:
        pass

    # 2. 信号筛选
    if allow_buy:
        scan_raw = collected_data.get("scan_watchlist", "{}")
        try:
            scan_data = json.loads(scan_raw) if isinstance(scan_raw, str) else scan_raw
            held_symbols = {c["symbol"] for c in candidates}
            for item in scan_data.get("watchlist_scan", []):
                sym = item.get("symbol", "")
                if sym in held_symbols: continue
                if item.get("needs_attention"):
                    candidates.append({"symbol": sym, "type": "SIGNAL"})
        except Exception:
            pass

    return candidates


# ═══════════════════════════════════════════
# Phase 3: Map 专家研报
# ═══════════════════════════════════════════

async def phase3_map_experts(
    candidates: list,
    orchestrator: ExpertOrchestrator,
    risk_result: dict,
    logger: logging.Logger,
) -> str:
    """
    Phase 3: 调用专家团生成决策简报
    """
    if not candidates:
        return "[]"

    logger.info(f"[Phase 3] 开始并行专家分析: {len(candidates)} 只个股")
    
    macro_briefing = {
        "risk_level": risk_result["regime"].upper(),
        "score": risk_result["score"],
        "summary": risk_result["constraints"]["message"],
        "key_events": []
    }

    tasks = [orchestrator.get_full_briefing(c["symbol"], macro_briefing) for c in candidates]
    briefings = await asyncio.gather(*tasks)
    
    return json.dumps(briefings, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════
# Phase 4: Reduce 战略决策
# ═══════════════════════════════════════════

async def phase4_strategic_decision(
    agent: ReActAgent,
    collected_data: dict,
    briefings_json: str,
    logger: logging.Logger,
) -> str:
    """
    Phase 4: 主决策智能体执行决策
    """
    logger.info("[Phase 4] 开始 CIO 战略决策推理")
    result = await agent.run(
        decision_briefings_json=briefings_json,
        pre_executed_data=collected_data
    )
    return result


# ═══════════════════════════════════════════
# Phase 5: 每日复盘
# ═══════════════════════════════════════════

def phase5_daily_review(review_agent: ReviewAgent, logger: logging.Logger):
    """
    Phase 5: 执行收盘复盘
    """
    logger.info("[Phase 5] 开始每日复盘分析")
    report = review_agent.run()
    logger.info(f"[Phase 5] 复盘完成，报告已生成并发送")
    return report



async def run_strategic_cycle(tool_registry, config, logger, orchestrator, agent, feishu_notifier, morning_briefing_sent, trade_logger):
    # Phase 1: 收集
    collected_data = phase1_collect_data(tool_registry, logger)
    # Phase 2: 风控
    risk_result = phase2_risk_scoring(collected_data, config, logger)
    # Phase 2.5: 候选
    candidates = phase2_5_extract_candidates(collected_data, risk_result, logger)
    # Phase 3: 专家 (Map)
    briefings_json = await phase3_map_experts(candidates, orchestrator, risk_result, logger)
    
    # 推送选股和专家分析到飞书 (开盘简报)
    if feishu_notifier and candidates and not morning_briefing_sent:
        risk_msg = f"🌡️ 【宏观风控简报】\n评分: {risk_result['score']} | 等级: {risk_result['regime']}\n核心逻辑: {risk_result['constraints']['message']}\n"
        
        cand_details = []
        for c in candidates:
            reason = "持仓巡检" if c.get("type") == "POSITION" else "发现交易信号"
            cand_details.append(f"• {c['symbol']} ({reason})")
        
        selection_msg = "🔍 【选股清单】\n" + "\n".join(cand_details)
        feishu_notifier.send_text(f"{risk_msg}\n{selection_msg}")
        morning_briefing_sent = True
    
    # Phase 4: 决策 (Reduce)
    result = await phase4_strategic_decision(agent, collected_data, briefings_json, logger)
    logger.info(f"本轮决策结论:\n{result}")
    return morning_briefing_sent

def watchdog_check(tool_registry, logger):
    """
    高频监控 (Watchdog) 层：纯本地计算，不调用大模型。
    功能：
    1. 获取当前所有持仓。
    2. 如果跌破成本价 8%，自动触发生存级市价单硬止损 (Hard Stop)。
    3. 如果盈利超 15%，触发简易追踪止盈（锁定 5% 利润的止损线）。
    """
    try:
        from tools.market_data import get_quote_ctx, modify_symbol
        
        pos_resp = tool_registry.execute("get_positions")
        pos_data = json.loads(pos_resp)
        positions = pos_data.get("positions", [])
        
        if not positions:
            return
            
        quote_ctx = get_quote_ctx()
        
        for pos in positions:
            symbol = pos["symbol"]
            qty = float(pos["quantity"])
            cost = float(pos["cost_price"])
            
            if qty <= 0:
                continue
                
            full_symbol = modify_symbol(symbol)
            quotes = quote_ctx.quote([full_symbol])
            if not quotes:
                continue
            
            current_price = float(quotes[0].last_done)
            
            # 1. 硬止损: 默认买入价跌去 8%
            hard_stop_price = cost * 0.92
            
            # 2. 简易保护止盈: 若已经暴涨超 15%，则底线提高到获利 5%
            stop_loss_price = cost * 1.05 if current_price > cost * 1.15 else hard_stop_price
            
            if current_price < stop_loss_price:
                action = "锁定利润" if stop_loss_price > cost else "硬止损割肉"
                logger.warning(f"🚨 [Watchdog] {symbol} 触发{action}! 最新价 ${current_price:.2f} 跌破警戒线 ${stop_loss_price:.2f} (成本: ${cost:.2f})")
                
                # 紧急呼叫市价单斩仓
                sell_resp = tool_registry.execute(
                    "sell_stock",
                    symbol=symbol,
                    quantity=int(qty),
                    order_type="MO",
                    reason=f"Watchdog 触发自动{action}: 跌破 {stop_loss_price:.2f}"
                )
                logger.info(f"[Watchdog] 斩仓结果: {sell_resp}")
                
    except Exception as e:
        logger.error(f"[Watchdog] 执行异常: {e}")

def main():
    # 信号处理
    def _graceful_shutdown(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    # 加载配置
    import os
    config = AppConfig.from_env(os.getenv("LLM_PROVIDER", "gemini"))
    config.longport.to_env()
    logger = setup_logger("strategic_agent", config.log)
    
    logger.info("=" * 60)
    logger.info("LP-Agent v3.0 (Watchdog + Strategic Brain) 启动")
    logger.info("=" * 60)

    try:
        primary_llm = create_llm(config.llm)
        analyst_llm = create_llm(config.analyst_llm) if config.analyst_llm else primary_llm
        logger.info(f"LLM初始化成功: Primary({primary_llm.model}), Analyst({analyst_llm.model})")
    except Exception as e:
        logger.error(f"LLM初始化失败: {e}")
        sys.exit(1)

    # 初始化组件
    tool_registry = ToolRegistry()
    tool_registry.register_all(create_trading_tools())
    tool_registry.register_all(create_market_data_tools())
    tool_registry.register_all(create_search_tools())

    orchestrator = ExpertOrchestrator(llm=analyst_llm)
    trading_memory = get_trading_memory()
    feishu_notifier = FeishuNotifier(webhook_url=config.feishu.webhook_url) if config.feishu.enabled else None

    agent = ReActAgent(
        llm=primary_llm,
        tool_registry=tool_registry,
        system_prompt=STRATEGIC_SYSTEM_PROMPT,
        max_iterations=10,
        feishu_notifier=feishu_notifier,
        trading_memory=trading_memory,
        logger=logger
    )

    review_agent = ReviewAgent(llm=primary_llm, config=config.review, feishu_notifier=feishu_notifier, trading_memory=trading_memory, logger_instance=logger)
    trade_logger = get_trade_logger()

    # ── 主循环 ──
    eastern = pytz.timezone('US/Eastern')
    last_date = None
    morning_briefing_sent = False
    
    # 记录当天是否执行过深度分析
    run_history = {"morning": False, "afternoon": False}

    while True:
        try:
            current_time = datetime.now(eastern)
            current_date = current_time.strftime('%Y-%m-%d')

            if current_date != last_date:
                review_agent.reset_daily_flag()
                last_date = current_date
                morning_briefing_sent = False
                run_history = {"morning": False, "afternoon": False}
                logger.info(f"新交易日: {current_date}")

            if not is_trading_hours(current_time):
                if review_agent.should_run():
                    phase5_daily_review(review_agent, logger)
                else:
                    logger.info("非交易时段，休眠中...")
                    time.sleep(60) # Sleep longer when market is closed
                continue
                
            # 交易时段: 运行高频 Watchdog
            watchdog_check(tool_registry, logger)

            # 交易时段: 判断是否需要唤醒深度决策层 (Strategic Brain)
            # 策略：每天 10:00 (开盘后消化完剧烈波动) 和 15:30 (收盘前确定日线形态)
            should_run_strategic = False
            time_str = current_time.strftime("%H:%M")
            
            if "10:00" <= time_str < "10:10" and not run_history["morning"]:
                logger.info("[Strategic Brain] 触发早盘深度决策时间 (10:00)")
                should_run_strategic = True
                run_history["morning"] = True
            elif "15:30" <= time_str < "15:40" and not run_history["afternoon"]:
                logger.info("[Strategic Brain] 触发尾盘深度决策时间 (15:30)")
                should_run_strategic = True
                run_history["afternoon"] = True
                
            if should_run_strategic:
                try:
                    loop = asyncio.get_event_loop()
                    morning_briefing_sent = loop.run_until_complete(
                        run_strategic_cycle(tool_registry, config, logger, orchestrator, agent, feishu_notifier, morning_briefing_sent, trade_logger)
                    )
                except Exception as e:
                    logger.error(f"深度决策层执行异常: {e}", exc_info=True)
                    trade_logger.log_error("cycle_error", str(e))

            # Watchdog 循环频率：1 分钟
            time.sleep(60)

        except KeyboardInterrupt:
            logger.info("收到中断信号，正在退出...")
            break
        except Exception as e:
            logger.error(f"主循环出错: {e}", exc_info=True)
            time.sleep(60)

    logger.info("LP-Agent v3.0 已退出")

if __name__ == "__main__":
    main()
