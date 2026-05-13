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
from typing import Optional, Dict, List, Any

import pytz
import holidays

from config import AppConfig, WATCHLIST
from logger import setup_logger
from llm.deepseek import DeepSeekLLM
from llm.gemini import GeminiLLM
from llm.base import ChatMessage, Role
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
            fallback_model=llm_config.fallback_model,
        )
    elif provider == "gemini":
        return GeminiLLM(
            api_key=llm_config.api_key,
            model=llm_config.model,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens,
            fallback_model=llm_config.fallback_model,
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

    # 2. 信号与动能筛选 (加入投资组合强弱对比逻辑)
    scan_raw = collected_data.get("scan_watchlist", "{}")
    try:
        scan_data = json.loads(scan_raw) if isinstance(scan_raw, str) else scan_raw
        scan_list = scan_data.get("watchlist_scan", [])
        
        # 获取各标的的动能/阶段情况
        symbol_momentum = {}
        for item in scan_list:
            sym = item.get("symbol", "")
            stage2 = item.get("stage2", False) # fix key
            ret_20d = item.get("return_20d_pct") or 0
            flags = item.get("flags", [])
            symbol_momentum[sym] = {"stage2": stage2, "ret_20d": ret_20d, "flags": flags, "price": item.get("price")}
            
        held_symbols = {c["symbol"] for c in candidates}
        
        # 标记极弱的持仓 (跌破Stage2且近期大跌)
        for c in candidates:
            if c["type"] == "POSITION":
                mom = symbol_momentum.get(c["symbol"])
                if mom:
                    c["details"] = mom
                if mom and not mom["stage2"] and mom["ret_20d"] < -5.0:
                    c["type"] = "WEAK_POSITION"
                    logger.warning(f"发现极弱持仓: {c['symbol']}, 准备提示 CIO 进行汰弱留强")

        # 若环境允许买入，提取极强信号
        if allow_buy:
            for item in scan_list:
                sym = item.get("symbol", "")
                if sym in held_symbols: continue
                # 如果是明确的 Stage 2 且近期表现强劲或有其他关注信号
                ret_20d = item.get("return_20d_pct") or 0
                if item.get("needs_attention") or (item.get("stage2") and ret_20d > 5.0):
                    cand = {"symbol": sym, "type": "STRONG_SIGNAL", "details": symbol_momentum.get(sym, {})}
                    candidates.append(cand)
                    
    except Exception as e:
        logger.error(f"Phase 2.5 解析 scan_watchlist 异常: {e}")

    # 排序：WEAK_POSITION 优先，STRONG_SIGNAL 其次，普通 POSITION 最后
    type_priority = {"WEAK_POSITION": 1, "STRONG_SIGNAL": 2, "SIGNAL": 3, "POSITION": 4}
    candidates.sort(key=lambda x: type_priority.get(x["type"], 99))
    
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
        "summary": risk_result.get("constraints", {}).get("message", "无明确约束信息"),
        "key_events": []
    }

    # 获取全局的板块轮动简报
    logger.info("[Phase 3] 获取全局板块分析 (Sector Analysis)...")
    sector_briefing = await orchestrator.get_sector_briefing()
    logger.info(f"板块轮动总结: {sector_briefing.get('summary')}")

    sem = asyncio.Semaphore(2) # 限制最高并发量，避免触发大模型限流和阻塞
    
    async def _analyze_with_sem(c):
        async with sem:
            return await orchestrator.get_full_briefing(c["symbol"], macro_briefing, sector_briefing)

    tasks = [_analyze_with_sem(c) for c in candidates]
    briefings = await asyncio.gather(*tasks)
    
    return json.dumps(briefings, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════
# Phase 4: Reduce 战略决策
# ═══════════════════════════════════════════

async def phase4_strategic_decision(
    agent: ReActAgent,
    collected_data: dict,
    briefings_json: str,
    risk_score: float,
    logger: logging.Logger,
    interrupt_events: Optional[str] = None
) -> str:
    """
    Phase 4: 主决策智能体执行决策
    """
    logger.info("[Phase 4] 开始 CIO 战略决策推理")
    result = await agent.run(
        decision_briefings_json=briefings_json,
        risk_score=risk_score,
        pre_executed_data=collected_data,
        interrupt_events=interrupt_events
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
        risk_msg = f"**评分:** {risk_result.get('score', 0)} | **等级:** {risk_result.get('regime', 'UNKNOWN')}\\n**核心逻辑:** {risk_result.get('constraints', {}).get('message', '无明确约束信息')}"
        
        cand_list = []
        for c in candidates:
            c_type = c.get("type", "")
            if c_type == "POSITION":
                reason = "🛡️ 持仓巡检"
            elif c_type == "WEAK_POSITION":
                reason = "⚠️ 极弱持仓"
            else:
                reason = "💡 交易信号"
            
            details = c.get("details", {})
            stage_str = "✅ Stage2" if details.get("stage2") else "❌ 非Stage2"
            flags = details.get("flags", [])
            flags_str = f" | 标签: {','.join(flags)}" if flags else ""
            ret = details.get('ret_20d')
            ret_str = f" | 20日涨幅: {ret}%" if ret is not None else ""
            
            cand_list.append(f"**{c['symbol']}** ({reason})\\n  └ {stage_str}{ret_str}{flags_str}")
            
        card_content = f"**🌡️ 宏观风控简报**\\n{risk_msg}\\n\\n**🔍 今日关注标的池**\\n" + "\\n".join(cand_list)
        
        color = "red" if risk_result['regime'] == "LOCKDOWN" else ("orange" if risk_result['regime'] == "CAUTIOUS" else "green")
        
        feishu_notifier.send_card(
            title="🌅 LP-Agent 早盘扫描与战略部署",
            content=card_content,
            color=color,
            footer="Phase 1 & 2: Market Scan & Risk Control"
        )
        morning_briefing_sent = True
    
    # Phase 4: 决策 (Reduce)
    result = await phase4_strategic_decision(agent, collected_data, briefings_json, risk_result["score"], logger)
    logger.info(f"本轮决策结论:\n{result}")
    return morning_briefing_sent

async def run_event_driven_cycle(events, tool_registry, config, logger, orchestrator, agent, feishu_notifier, trade_logger):
    """
    事件驱动循环：处理 Watchdog 抛出的日内高优中断事件
    """
    logger.info("============== ⚡ 事件驱动紧急避险/进攻 (Event-Driven Cycle) ==============")
    
    candidates = []
    event_msgs = []
    for e in events:
        # 将事件直接强行变成候选股
        candidates.append({"symbol": e["symbol"], "type": "EVENT_TRIGGER", "weight": 100})
        event_msgs.append(f"- [{e['symbol']}] {e['type']}: {e['reason']}")
        
    event_str = "\n".join(event_msgs)
    if feishu_notifier:
        feishu_notifier.send_text(f"🚨 【Watchdog 雷达预警】\n发现 {len(events)} 个异动事件:\n{event_str}\n\n🤖 主脑(CIO)已介入，正在紧急研判...")

    # 1. 收集全局上下文
    collected_data = phase1_collect_data(tool_registry, logger)
    # 2. 依然进行宏观打分，避免逆势
    risk_result = phase2_risk_scoring(collected_data, config, logger)
    # 3. 让专家对涉及异动的股票出具报告
    logger.info(f"触发异动的标的: {[c['symbol'] for c in candidates]}，唤醒专家...")
    briefings_json = await phase3_map_experts(candidates, orchestrator, risk_result, logger)
    
    # 4. CIO决策，传入特殊的 interrupt_events 参数
    logger.info("呼叫 CIO 进行事件应对决策...")
    result = await phase4_strategic_decision(agent, collected_data, briefings_json, risk_result["score"], logger, interrupt_events=event_str)
    logger.info(f"事件驱动决策结论:\n{result}")

def calculate_rsi_from_candles(candles, period=14):
    if len(candles) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(candles)):
        change = float(candles[i].close) - float(candles[i-1].close)
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ==========================================
# WebSocket 实时事件驱动流 (Millisecond Watchdog)
# ==========================================
import threading
from longport.openapi import SubType, PushQuote
from tools.market_data import get_quote_ctx, modify_symbol
import config as app_config

_ws_events = []
_ws_lock = threading.Lock()
_global_pos_map = {}  # 由主循环定期更新持仓成本

def on_quote_push(symbol: str, event: PushQuote):
    """WebSocket 毫秒级回调，侦测极端异动"""
    try:
        clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol
        current_price = float(event.last_done)
        
        # 读取持仓成本
        cost_price = None
        if clean_symbol in _global_pos_map:
            cost_price = float(_global_pos_map[clean_symbol].get("cost_price", 0))
            
        # 1. 致命跌破熔断: 瞬间跌破成本 8% (防崩盘)
        if cost_price and current_price < cost_price * 0.92:
            with _ws_lock:
                _ws_events.append({
                    "symbol": clean_symbol,
                    "type": "DEFENSIVE_DROP_WS",
                    "reason": f"[WS毫秒级拦截] 最新价 ${current_price:.2f} 瞬间跌破持仓成本 ${cost_price:.2f} 的 8%，系统极速熔断报警！"
                })
        
        # 2. 毫秒级动能突破: 突破日内最高点 (抢跑)
        day_high = float(event.high)
        if day_high > 0 and current_price >= day_high * 0.998:
            with _ws_lock:
                # 避免同一只股票疯狂发事件
                if not any(e["symbol"] == clean_symbol and "BREAKOUT" in e["type"] for e in _ws_events):
                    _ws_events.append({
                        "symbol": clean_symbol,
                        "type": "OFFENSIVE_BREAKOUT_WS",
                        "reason": f"[WS毫秒级拦截] 标的瞬间打穿日内高点 ${day_high:.2f}，资金净抢筹！"
                    })
    except Exception:
        pass

def init_websocket_subscriptions(logger):
    """初始化并挂载 WebSocket"""
    try:
        ctx = get_quote_ctx()
        ctx.set_on_quote(on_quote_push)
        
        # 订阅当前 Watchlist 标的
        full_symbols = [modify_symbol(s) for s in app_config.WATCHLIST]
        if full_symbols:
            ctx.subscribe(full_symbols, [SubType.Quote], is_first_push=True)
            logger.info(f"⚡ [WebSocket] 已成功订阅 {len(full_symbols)} 只标的的毫秒级深度行情！")
    except Exception as e:
        logger.error(f"[WebSocket] 订阅失败: {e}")


def watchdog_check(tool_registry, logger):
    """
    高频监控雷达 (Watchdog)：纯本地计算
    功能：发现右侧突破、短期衰竭、跌破支撑等事件，并将决策权移交 CIO。
    """
    events = []
    try:
        import config as app_config
        from tools.market_data import get_quote_ctx, modify_symbol
        from longport.openapi import Period, AdjustType
        
        # 获取最新的持仓，并更新给 WebSocket 线程共享
        pos_resp = tool_registry.execute("get_positions")
        pos_data = json.loads(pos_resp)
        positions = pos_data.get("positions", [])
        
        pos_map = {p["symbol"]: p for p in positions if float(p.get("quantity", 0)) > 0}
        
        global _global_pos_map
        _global_pos_map = pos_map
        
        # --- 读取并清空 WebSocket 瞬间捕获的事件 ---
        with _ws_lock:
            global _ws_events
            if _ws_events:
                events.extend(_ws_events)
                _ws_events = []
        
        # 监控范围：当前持仓 + Watchlist
        monitor_symbols = set(list(pos_map.keys()) + app_config.WATCHLIST)
        
        if not monitor_symbols:
            return events
            
        quote_ctx = get_quote_ctx()
        
        for symbol in monitor_symbols:
            full_symbol = modify_symbol(symbol)
            quotes = quote_ctx.quote([full_symbol])
            if not quotes:
                continue
                
            quote = quotes[0]
            current_price = float(quote.last_done)
            day_high = float(quote.high)
            day_low = float(quote.low)
            
            # 获取 15 分钟 K 线计算 RSI
            candles = quote_ctx.history_candlesticks_by_offset(full_symbol, Period.Min_15, AdjustType.ForwardAdjust, True, 20)
            rsi_15 = calculate_rsi_from_candles(candles, 14) if candles else 50.0
            
            # 1. 检查当前持仓
            if symbol in pos_map:
                cost = float(pos_map[symbol]["cost_price"])
                # 1.1 防守：跌破成本 8%
                if current_price < cost * 0.92:
                    events.append({
                        "symbol": symbol,
                        "type": "DEFENSIVE_DROP",
                        "reason": f"最新价 ${current_price:.2f} 已跌破持仓成本 ${cost:.2f} 的 8%，需要紧急评估止损！"
                    })
                # 1.2 做T：暴涨锁润
                elif current_price > cost * 1.15 and rsi_15 > 80:
                    events.append({
                        "symbol": symbol,
                        "type": "SWING_EXHAUSTION",
                        "reason": f"持仓盈利已超 15% 且 15分钟RSI({rsi_15:.1f})极度超买，动能可能衰竭，建议逢高卖出部分做T锁定利润。"
                    })
            
            # 2. 检查 Watchlist 和持仓的右侧突破
            # 当日涨幅突破或接近日内高点，且 RSI 强势
            if day_high > 0 and current_price >= day_high * 0.995 and day_high > day_low * 1.01:
                if 60 < rsi_15 < 85: # 强势但还未极度超买
                    events.append({
                        "symbol": symbol,
                        "type": "OFFENSIVE_BREAKOUT",
                        "reason": f"股价(${current_price:.2f})接近或突破日内高点(${day_high:.2f})，15分钟RSI({rsi_15:.1f})强势，存在右侧动能爆发可能！"
                    })
                    
    except Exception as e:
        logger.error(f"[Watchdog] 雷达扫描异常: {e}", exc_info=True)
        
    return events

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

    feishu_notifier = FeishuNotifier(
        webhook_url=config.feishu.webhook_url,
        app_id=config.feishu.app_id,
        app_secret=config.feishu.app_secret
    ) if config.feishu.enabled else None

    orchestrator = ExpertOrchestrator(llm=analyst_llm, feishu_notifier=feishu_notifier)
    trading_memory = get_trading_memory()

    agent = ReActAgent(
        llm=primary_llm,
        tool_registry=tool_registry,
        system_prompt=STRATEGIC_SYSTEM_PROMPT,
        max_iterations=10,
        feishu_notifier=feishu_notifier,
        trading_memory=trading_memory,
        logger=logger
    )


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

    # 初始化 WebSocket 毫秒级监控
    init_websocket_subscriptions(logger)


    # ── 主循环 ──
    eastern = pytz.timezone('US/Eastern')
    last_date = None
    morning_briefing_sent = False
    
    # 记录当天是否执行过深度分析
    run_history = {"morning": False, "afternoon": False}
    scanner_ran = False
    
    # News Watchdog 相关状态
    known_critical_events = ""
    last_critical_alert_time = None

    while True:
        try:
            current_time = datetime.now(eastern)
            current_date = current_time.strftime('%Y-%m-%d')

            if current_date != last_date:
                review_agent.reset_daily_flag()
                last_date = current_date
                morning_briefing_sent = False
                run_history = {"morning": False, "afternoon": False}
                scanner_ran = False
                logger.info(f"新交易日: {current_date}")

            # 盘前 9:00 - 9:30 执行 Alpha Scanner 获取动态标的池
            time_str = current_time.strftime("%H:%M")
            if "09:00" <= time_str < "09:30" and not scanner_ran:
                try:
                    from agent.alpha_scanner import AlphaScanner
                    scanner = AlphaScanner(llm=analyst_llm)
                    new_watchlist, scan_reason = scanner.run()

                    import config as app_config
                    app_config.update_watchlist_in_place(new_watchlist)
                    logger.info(f"[Phase 0] Alpha Scanner 完毕，当前内存标的池已更新为: {app_config.WATCHLIST}")

                    if feishu_notifier:
                        feishu_notifier.send_card(
                            title="🌅 每日动态标的池更新",
                            content=f"**选股逻辑：**\n{scan_reason}\n\n**今日监控名单：**\n`{', '.join(new_watchlist)}`",
                            color="turquoise"
                        )
                except Exception as e:
                    logger.error(f"Alpha Scanner 运行异常: {e}")
                scanner_ran = True

            if not is_trading_hours(current_time):
                if review_agent.should_run():
                    phase5_daily_review(review_agent, logger)
                else:
                    logger.info("非交易时段，休眠中...")
                    time.sleep(60) # Sleep longer when market is closed
                continue
                
            # 交易时段: 运行高频 Watchdog
            watchdog_events = watchdog_check(tool_registry, logger)
            if watchdog_events:
                logger.warning(f"🚨 Watchdog 触发了 {len(watchdog_events)} 个中断事件! 准备唤醒 CIO...")
                try:
                    loop = asyncio.get_event_loop()
                    loop.run_until_complete(
                        run_event_driven_cycle(watchdog_events, tool_registry, config, logger, orchestrator, agent, feishu_notifier, trade_logger)
                    )
                except Exception as e:
                    logger.error(f"事件驱动循环执行异常: {e}", exc_info=True)

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

            # --- Event-Driven News Watchdog ---
            # 每 15 分钟扫一次核弹级新闻 (只有在盘中且非深度决策时执行)
            current_minute = current_time.minute
            if not should_run_strategic and current_minute % 15 == 0:
                try:
                    # 检查是否处于冷却期 (2小时内触发过，则跳过)
                    if last_critical_alert_time and (current_time - last_critical_alert_time).total_seconds() < 7200:
                        logger.debug("[News Watchdog] 仍在避险冷却期内，跳过本次巡检。")
                    else:
                        logger.info("[News Watchdog] 执行盘中突发新闻巡检...")
                        news_client = tool_registry.get("search_macro_economics") # 或者用 get_search_client
                        if news_client:
                            # 借用 LLM 做快速情感判断
                            news_res = news_client.execute(topic="US stock market breaking news crash emergency surprise")

                            prompt = "分析以下最新市场新闻，是否包含 **刚刚发生（如过去数小时内）**、且可能立即引发美股大盘暴跌的盘中核弹级突发事件（如突发紧急降息、刚刚爆发的新战争冲突等）？\n"
                            if known_critical_events:
                                prompt += f"注意：以下是我们【已经知晓且已处理】的事件：\n{known_critical_events}\n如果新闻只是报道这些事件的延续，请严格回复 'NORMAL'。\n"

                            prompt += "只有遇到全新的、意料之外的盘中黑天鹅，才回复 'CRITICAL: [事件简述]'。如果没有此类全新突发事件，仅回复 'NORMAL'。\n\n新闻:\n" + news_res[:2000]

                            messages = [
                                ChatMessage(role=Role.SYSTEM, content="You are a fast market news sentiment detector."),
                                ChatMessage(role=Role.USER, content=prompt)
                            ]
                            alert_resp = analyst_llm.chat(messages).content.strip()

                            if alert_resp.startswith("CRITICAL"):
                                logger.error(f"🚨 [News Watchdog] 侦测到突发核弹级事件: {alert_resp}。强行唤醒 CIO 进行计划外避险决策！")
                                # 更新状态
                                last_critical_alert_time = current_time
                                known_critical_events += f"- {alert_resp}\n"

                                # 强行拉起深度决策层
                                loop = asyncio.get_event_loop()
                                # 为了速度，直接跳过选股，强行对持仓进行避险评估
                                collected_data = phase1_collect_data(tool_registry, logger)
                                risk_result = {"score": 0.0, "regime": "PANIC", "reason": alert_resp, "constraints": {"allow_new_buy": False, "must_reduce": True, "message": alert_resp}}
                                emergency_candidates = phase2_5_extract_candidates(collected_data, risk_result, logger)
                                emergency_candidates = [c for c in emergency_candidates if c["type"] == "POSITION"] # 只管手里的票
                                if emergency_candidates:
                                    emergency_briefings = loop.run_until_complete(
                                        phase3_map_experts(emergency_candidates, orchestrator, risk_result, logger)
                                    )
                                    loop.run_until_complete(
                                        phase4_strategic_decision(agent, collected_data, emergency_briefings, 0.0, logger, interrupt_events=alert_resp)
                                    )
                except Exception as e:
                    logger.error(f"[News Watchdog] 巡检异常: {e}", exc_info=True)

            # Watchdog 循环频率：动态计算休眠时间，对齐到下一个整分钟，防止执行耗时导致时间漂移
            now = datetime.now()
            sleep_sec = 60 - now.second
            time.sleep(sleep_sec)

        except KeyboardInterrupt:
            logger.info("收到中断信号，正在退出...")
            break
        except Exception as e:
            logger.error(f"主循环出错: {e}", exc_info=True)
            time.sleep(60)

    logger.info("LP-Agent v3.0 已退出")

if __name__ == "__main__":
    main()
