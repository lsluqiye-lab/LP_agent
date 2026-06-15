"""
LP-Agent v4.2 主入口 (Strategic Multi-Agent)
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
    检查是否在交易监控时段（覆盖盘前防御 + 盘中决策 + 盘后复盘）
    8:00 - 17:00 ET
    """
    nyse_holidays = holidays.NYSE()
    today_str = eastern_time.strftime('%Y-%m-%d')
    if nyse_holidays.get(today_str):
        return False
    if eastern_time.weekday() >= 5:
        return False
    current_t = eastern_time.time()
    # 扩大窗口至 8:00 - 17:00 ET，确保盘前黑天鹅防御和盘后对冲对齐
    if dt_time(8, 0) <= current_t <= dt_time(17, 0):
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


def cleanup_stale_orders(collected_data: dict, tool_registry: ToolRegistry, logger: logging.Logger):
    """
    清理陈旧或逻辑冲突的挂单 (Daily Cleanup)
    """
    logger.info("[Cleanup] 正在扫描并清理陈旧挂单...")
    try:
        # 获取持仓
        pos_raw = collected_data.get("get_positions", "{}")
        positions_data = json.loads(pos_raw)
        positions = positions_data.get("positions", []) if isinstance(positions_data, dict) else []
        held_symbols = {p["symbol"] for p in positions if float(p.get("quantity", 0)) > 0}
        
        # 获取今日订单
        orders_raw = collected_data.get("get_today_orders", "{}")
        orders_data = json.loads(orders_raw)
        orders = orders_data.get("orders", []) if isinstance(orders_data, dict) else []
        
        cancelled_count = 0
        for o in orders:
            # 仅处理挂单中的订单 (PENDING/NEW/WAITING/SUBMITTED 等)
            status = str(o.get("status", "")).upper()
            if not any(s in status for s in ["PENDING", "NEW", "WAITING", "SUBMITTED", "VARIETIESNOTREPORTED"]):
                continue
                
            symbol = o["symbol"]
            side = str(o["side"]).upper()
            order_id = o.get("order_id")
            
            if not order_id:
                continue

            # 1. 清理幽灵卖单: 无持仓但挂着卖单 (包括止损单)
            if "SELL" in side and symbol not in held_symbols:
                logger.warning(f"[Cleanup] 发现幽灵卖单: {symbol} 无持仓，正在撤销订单 {order_id}...")
                tool_registry.execute("cancel_order", order_id=order_id, reason="Daily Cleanup: Ghost sell order without position")
                cancelled_count += 1
                
        if cancelled_count > 0:
            logger.info(f"[Cleanup] 清理完成，共撤销 {cancelled_count} 笔陈旧挂单。")
        else:
            logger.info("[Cleanup] 未发现陈旧挂单，账户状态干净。")
            
    except Exception as e:
        logger.error(f"[Cleanup] 执行失败: {e}")


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

def phase2_5_extract_candidates(collected_data: dict, risk_result: dict, logger: logging.Logger, portfolio_directives: Optional[dict] = None) -> list:
    """
    Phase 2.5: 提取需要专家分析的候选标的
    """
    logger.info("[Phase 2.5] 提取行动候选清单")
    candidates = []
    allow_buy = risk_result.get("constraints", {}).get("allow_new_buy", False)
    
    weed_out_list = portfolio_directives.get("weed_out_list", []) if portfolio_directives else []

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
        
        # 标记极弱的持仓 (跌破Stage2且近期大跌，或被组合管理器标记为 weed)
        for c in candidates:
            if c["type"] == "POSITION":
                mom = symbol_momentum.get(c["symbol"])
                if mom:
                    c["details"] = mom
                if c["symbol"] in weed_out_list:
                    c["type"] = "WEAK_POSITION"
                    logger.warning(f"发现极弱持仓(被PortfolioManager淘汰): {c['symbol']}, 准备提示 CIO 进行汰弱留强")
                elif mom and not mom["stage2"] and mom["ret_20d"] < -5.0:
                    c["type"] = "WEAK_POSITION"
                    logger.warning(f"发现极弱持仓(技术面破位): {c['symbol']}, 准备提示 CIO 进行汰弱留强")

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
    
    # 🆕 升级：不再仅使用浅层的 risk_result，而是调用 MacroAnalyst 进行深度本质分析
    logger.info("[Phase 3] 获取深度宏观结构化研报 (Macro Deep Analysis)...")
    macro_deep_report = await orchestrator.get_macro_deep_briefing()
    
    macro_briefing = {
        "risk_level": risk_result["regime"].upper(),
        "score": risk_result["score"],
        "summary": risk_result.get("constraints", {}).get("message", "无明确约束信息"),
        "deep_analysis": macro_deep_report, # 注入深度分析
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
    portfolio_directives: Optional[dict] = None,
    interrupt_events: Optional[str] = None
) -> str:
    """
    Phase 4: 主决策智能体执行决策
    """
    logger.info("[Phase 4] 开始 CIO 战略决策推理")
    result = await agent.run(
        decision_briefings_json=briefings_json,
        risk_score=risk_score,
        portfolio_directives=portfolio_directives,
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


def pre_screen_candidates(candidates: list, risk_result: dict, portfolio_directives: dict, collected_data: dict, logger: logging.Logger) -> tuple[list, list]:
    """
    对行动候选股（candidates）进行本地硬规则预筛选 (Local Pre-Screening)
    返回：(active_candidates, passive_candidates)
    active_candidates: 触发活跃交易信号、需要启动大模型专家分析与 CIO 决策的个股列表
    passive_candidates: 状态平稳、无需任何交易动作的股票代码列表 (Auto-HOLD)
    """
    logger.info("[Pre-Screening] 开始本地硬规则门限初筛...")
    
    active_candidates = []
    passive_candidates = []
    
    allow_buy = risk_result.get("constraints", {}).get("allow_new_buy", False)
    macro_score = risk_result.get("score", 50.0)
    weed_out_list = portfolio_directives.get("weed_out_list", []) if portfolio_directives else []

    # 1. 提取当前持仓及浮盈率
    pos_raw = collected_data.get("get_positions", "{}")
    try:
        pos_data = json.loads(pos_raw) if isinstance(pos_raw, str) else pos_raw
        positions = pos_data.get("positions", [])
    except Exception:
        positions = []

    # 提取总资产 (Net Assets) 与单股仓位限制，用于进行超量持仓风控拦截
    acct_raw = collected_data.get("get_account_balance", "{}")
    try:
        acct_bal = json.loads(acct_raw) if isinstance(acct_raw, str) else acct_raw
        net_assets = float(acct_bal.get("net_assets", 0.0))
    except Exception:
        net_assets = 0.0

    max_stock_limit = 15.0  # 默认单股上限
    if portfolio_directives and "dynamic_limits" in portfolio_directives:
        max_stock_limit = float(portfolio_directives["dynamic_limits"].get("max_single_stock_exposure_pct", 15.0))
        
    pos_profit_map = {}
    for p in positions:
        sym = p.get("symbol")
        try:
            cost = float(p.get("cost_price", 0))
            qty = float(p.get("quantity", 0))
            # 优先使用实时现价（last_done）进行准确利润及市值计算
            p_last = p.get("last_done")
            cur_price = float(p_last) if p_last and p_last != "N/A" else cost
            profit_pct = (cur_price / cost - 1) * 100 if cost > 0 else 0.0
            pos_profit_map[sym] = profit_pct
        except Exception:
            pos_profit_map[sym] = 0.0

    # 2. 逐一判定门限
    for c in candidates:
        sym = c["symbol"]
        c_type = c["type"]
        details = c.get("details", {}) or {}
        
        # 提取技术状态
        price = details.get("price") or 0.0
        ret_20d = details.get("ret_20d") or 0.0
        flags = details.get("flags") or []
        vol_ratio = details.get("volume_ratio") or 1.0
        
        is_active = False
        active_reason = ""
        
        # ── A. 持仓股票诊断门限 ──
        if c_type in ["POSITION", "WEAK_POSITION"]:
            # 门限 A0: 单股持仓比例严重超过自适应上限 (例如超标 1.5% 以上)，强制触发 CIO 减仓决策
            pos_item = next((p for p in positions if p.get("symbol") == sym), None)
            if pos_item and net_assets > 0:
                try:
                    p_qty = float(pos_item.get("quantity", 0.0))
                    p_last = pos_item.get("last_done")
                    p_price = float(p_last) if p_last and p_last != "N/A" else float(pos_item.get("cost_price", 0.0))
                    p_exposure_pct = (p_qty * p_price) / net_assets * 100
                    if p_exposure_pct > max_stock_limit + 1.5:  # 给予 1.5% 的安全震荡及上涨缓冲
                        is_active = True
                        active_reason = f"单股持仓比例({p_exposure_pct:.2f}%)严重超标（当前自适应风控上限为 {max_stock_limit:.2f}%），必须强行触发 CIO 进行减仓/仓位再平衡决策。"
                except Exception as e:
                    logger.error(f"计算 {sym} 仓位占比超标预筛异常: {e}")

            if not is_active:
                # 门限 A1: 被 PortfolioManager 标记为需要淘汰的弱势杂草 (Weed out)
                if sym in weed_out_list or c_type == "WEAK_POSITION":
                    is_active = True
                    active_reason = "被标记为需要淘汰的弱势持仓，需 CIO 进行优胜劣汰决策。"
                    
                # 门限 A2: 宏观风控大跌 (Score < 50)，强制收紧持仓防守
                elif macro_score < 50:
                    is_active = True
                    active_reason = f"宏观风控评分({macro_score})大跌进入防御，必须强制收紧防守线。"
                    
                # 门限 A3: 浮盈不高（浮盈 < 2.0% 且近期大幅回撤 ret_20d < -4.0%），利润保护触发
                else:
                    profit_pct = pos_profit_map.get(sym, 0.0)
                    if profit_pct < 2.0 and ret_20d < -4.0:
                        is_active = True
                        active_reason = f"持仓浮盈过低({profit_pct:.2f}%)，且20日走势回落严重({ret_20d}%)，需锁定微薄利润防洗盘。"
                    
                    # 门限 A4: 盈利加仓（浮盈 >= 0.0%，且今日爆量拉升 vol_ratio > 1.3）
                    elif profit_pct >= 0.0 and (vol_ratio >= 1.3 or "HIGH_VOL" in flags):
                        is_active = True
                        active_reason = f"底仓浮盈({profit_pct:.2f}%)，且个股今日放量异动(量比 {vol_ratio}x)，存在金字塔加仓机会。"

        # ── B. 监控池股票买入诊断门限 ──
        elif c_type == "STRONG_SIGNAL" and allow_buy:
            # 门限 B1: 出现明确右侧向上突破（量比 > 1.3 或 needs_attention / MACD 黄金交叉等）
            if details.get("needs_attention") or vol_ratio >= 1.3 or "HIGH_VOL" in flags:
                is_active = True
                active_reason = f"监控股出现放量突破信号 (量比 {vol_ratio}x, 20日涨幅 {ret_20d}%)，触发右侧潜在买点。"

        if is_active:
            c["active_reason"] = active_reason
            active_candidates.append(c)
            logger.warning(f"🎯 [Pre-Screening] 标的 {sym} 触发活跃决策门限: {active_reason}")
        else:
            passive_candidates.append(sym)
            logger.info(f"💤 [Pre-Screening] 标的 {sym} 状态极其平稳 (Auto-HOLD)，保持观望。")

    logger.info(f"[Pre-Screening] 过滤结果: 活跃决策个股 {len(active_candidates)} 只, 观望个股 {len(passive_candidates)} 只")
    return active_candidates, passive_candidates


async def run_strategic_cycle(tool_registry, config, logger, orchestrator, agent, feishu_notifier, morning_briefing_sent, trade_logger):
    # Phase 1: 收集
    collected_data = phase1_collect_data(tool_registry, logger)
    
    # 🚨 新增：每日大扫除 - 清理陈旧挂单
    cleanup_stale_orders(collected_data, tool_registry, logger)
    
    # Phase 2: 风控
    risk_result = phase2_risk_scoring(collected_data, config, logger)
    
    # 🆕 Phase 2.1: 投资组合管理 (Portfolio Management)
    portfolio_directives = {}
    try:
        from agent.portfolio_manager import PortfolioManager
        pm = PortfolioManager()
        pos_raw = collected_data.get("get_positions", "{}")
        current_pos = json.loads(pos_raw).get("positions", []) if isinstance(pos_raw, str) else pos_raw.get("positions", [])
        acct_raw = collected_data.get("get_account_balance", "{}")
        acct_bal = json.loads(acct_raw) if isinstance(acct_raw, str) else acct_raw
        portfolio_directives = pm.analyze_portfolio(current_pos, acct_bal, risk_result)
        logger.info(f"[Phase 2.1] Portfolio Directives: {portfolio_directives}")
    except Exception as e:
        logger.error(f"Portfolio Manager 执行异常: {e}")
        
    # Phase 2.5: 候选
    candidates = phase2_5_extract_candidates(collected_data, risk_result, logger, portfolio_directives)
    
    # 🆕 Phase 2.6: 本地硬规则预筛选 (Local Pre-Screening)
    active_candidates, passive_candidates = pre_screen_candidates(candidates, risk_result, portfolio_directives, collected_data, logger)
    
    # 门限唤醒控制：若无任何活跃候选股，则整个流程自动判定无交易动作并跳过
    if not active_candidates:
        logger.warning("😴 [Pre-Screening] 本轮没有触发任何主动交易（买入/卖出/加仓/止损收紧）门限！资产组合整体极其平稳。")
        logger.info("[Pre-Screening] 系统决定保持观望，自动跳过大模型专家研报及 CIO ReAct 决策，本次巡检消耗 0 Token。")
        
        if feishu_notifier:
            today_str = datetime.now(pytz.timezone("US/Eastern")).strftime("%H:%M")
            feishu_notifier.send_text(
                f"🌅 【LP-Agent 正常巡检 | {today_str} ET】\n"
                f"当前持仓与监控池标的表现极其稳健，未触发任何买入、卖出、加仓或止损警报门限。\n"
                f"🤖 主脑(CIO)自动保持观望状态，本次巡检耗费 **0 Token**！"
            )
        return morning_briefing_sent

    # Phase 3: 专家 (Map) - 仅对活跃候选股进行专家分析，极大节约 Token！
    briefings_json = await phase3_map_experts(active_candidates, orchestrator, risk_result, logger)
    
    # 推送选股和专家分析到飞书 (开盘简报)
    if feishu_notifier and active_candidates and not morning_briefing_sent:
        risk_msg = f"**评分:** {risk_result.get('score', 0)} | **等级:** {risk_result.get('regime', 'UNKNOWN')}\\n**核心逻辑:** {risk_result.get('constraints', {}).get('message', '无明确约束信息')}"
        
        cand_list = []
        for c in active_candidates:
            c_type = c.get("type", "")
            reason = f"🎯 活跃决策: {c.get('active_reason', '')}"
            
            details = c.get("details", {})
            stage_str = "✅ Stage2" if details.get("stage2") else "❌ 非Stage2"
            flags = details.get("flags", [])
            flags_str = f" | 标签: {','.join(flags)}" if flags else ""
            ret = details.get('ret_20d')
            ret_str = f" | 20日涨幅: {ret}%" if ret is not None else ""
            
            cand_list.append(f"**{c['symbol']}**\\n  └ {reason}\\n  └ {stage_str}{ret_str}{flags_str}")
            
        pm_msg = ""
        if portfolio_directives.get("weed_out_list"):
            pm_msg = f"\\n**🥀 建议淘汰弱势持仓:** {','.join(portfolio_directives['weed_out_list'])}"
            
        card_content = f"**🌡️ 宏观风控简报**\\n{risk_msg}{pm_msg}\\n\\n**🔍 今日关注活跃标的池**\\n" + "\\n".join(cand_list)
        
        color = "red" if risk_result['regime'] == "LOCKDOWN" else ("orange" if risk_result['regime'] == "CAUTIOUS" else "green")
        
        feishu_notifier.send_card(
            title="🌅 LP-Agent 早盘扫描与活跃部署",
            content=card_content,
            color=color,
            footer="Phase 1, 2 & 2.6: Market Scan & Pre-Screening"
        )
        morning_briefing_sent = True
    
    # Phase 4: 决策 (Reduce) - 仅把活跃的专家研报和相关的组合指令发给 CIO 决策
    result = await phase4_strategic_decision(agent, collected_data, briefings_json, risk_result["score"], logger, portfolio_directives)
    logger.info(f"本轮决策结论:\n{result}")

    # ── 新增: 后置追踪止损全自动重整对齐 ──
    try:
        from tools.trading import auto_align_trailing_stops
        auto_align_trailing_stops()
    except Exception as e_align:
        logger.error(f"[Alignment] 自动对齐追踪止损执行失败: {e_align}")

    return morning_briefing_sent

async def run_event_driven_cycle(events, tool_registry, config, logger, orchestrator, agent, feishu_notifier, trade_logger):
    """
    事件驱动循环：处理 Watchdog 抛出的日内高优中断事件
    """
    logger.info("============== ⚡ 事件驱动紧急避险/进攻 (Event-Driven Cycle) ==============")
    
    candidates = []
    event_msgs = []
    seen_symbols = set()
    for e in events:
        if e["symbol"] not in seen_symbols:
            # 将事件直接强行变成候选股
            candidates.append({"symbol": e["symbol"], "type": "EVENT_TRIGGER", "weight": 100})
            seen_symbols.add(e["symbol"])
        event_msgs.append(f"- [{e['symbol']}] {e['type']}: {e['reason']}")
        
    event_str = "\n".join(event_msgs)
    if feishu_notifier:
        feishu_notifier.send_text(f"🚨 【Watchdog 雷达预警】\n发现 {len(events)} 个异动事件:\n{event_str}\n\n🤖 主脑(CIO)已介入，正在紧急研判...")

    # 1. 收集全局上下文
    collected_data = phase1_collect_data(tool_registry, logger)
    
    # 🚨 新增：每日大扫除 - 清理陈旧挂单
    cleanup_stale_orders(collected_data, tool_registry, logger)

    # 2. 依然进行宏观打分，避免逆势
    risk_result = phase2_risk_scoring(collected_data, config, logger)
    
    # 2.1 获取组合指令 (可选，应对事件)
    portfolio_directives = {}
    try:
        from agent.portfolio_manager import PortfolioManager
        pm = PortfolioManager()
        pos_raw = collected_data.get("get_positions", "{}")
        current_pos = json.loads(pos_raw).get("positions", []) if isinstance(pos_raw, str) else pos_raw.get("positions", [])
        acct_raw = collected_data.get("get_account_balance", "{}")
        acct_bal = json.loads(acct_raw) if isinstance(acct_raw, str) else acct_raw
        portfolio_directives = pm.analyze_portfolio(current_pos, acct_bal, risk_result)
    except Exception:
        pass

    # 3. 让专家对涉及异动的股票出具报告
    logger.info(f"触发异动的标的: {[c['symbol'] for c in candidates]}，唤醒专家...")
    briefings_json = await phase3_map_experts(candidates, orchestrator, risk_result, logger)
    
    # 4. CIO决策，传入特殊的 interrupt_events 参数
    logger.info("呼叫 CIO 进行事件应对决策...")
    result = await phase4_strategic_decision(
        agent, collected_data, briefings_json, risk_result["score"], logger, portfolio_directives=portfolio_directives, interrupt_events=event_str
    )
    logger.info(f"事件驱动决策结论:\n{result}")

    # ── 新增: 后置追踪止损全自动重整对齐 ──
    try:
        from tools.trading import auto_align_trailing_stops
        auto_align_trailing_stops()
    except Exception as e_align:
        logger.error(f"[Alignment] 自动对齐追踪止损执行失败: {e_align}")


def _get_daily_atr(quote_ctx, full_symbol, period=14):
    from tools.market_data import calc_atr
    from longport.openapi import Period, AdjustType
    try:
        daily_candles = quote_ctx.history_candlesticks_by_offset(full_symbol, Period.Day, AdjustType.ForwardAdjust, True, period + 1)
        if daily_candles and len(daily_candles) > period:
            highs = [float(c.high) for c in daily_candles]
            lows = [float(c.low) for c in daily_candles]
            closes = [float(c.close) for c in daily_candles]
            atr = calc_atr(highs, lows, closes, period)
            return atr
    except Exception as e:
        import logging
        logging.warning(f"获取 {full_symbol} ATR 失败: {e}")
    return None

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
_last_monitored_symbols = set()  # 记录上一次订阅的标的，用于增量订阅同步

# Watchdog 冷却控制，避免同一标的同一事件反复触发 CIO
_watchdog_cooldowns = {}
_cooldown_lock = threading.Lock()
WATCHDOG_COOLDOWN_SECONDS = 7200  # 冷却期 2 小时

def _is_cooldown(symbol: str, event_type: str) -> bool:
    """检查是否在冷却期内"""
    import time
    key = f"{symbol}_{event_type}"
    with _cooldown_lock:
        last_time = _watchdog_cooldowns.get(key, 0)
        if time.time() - last_time < WATCHDOG_COOLDOWN_SECONDS:
            return True
        return False

def _set_cooldown(symbol: str, event_type: str):
    """设置冷却时间"""
    import time
    key = f"{symbol}_{event_type}"
    with _cooldown_lock:
        _watchdog_cooldowns[key] = time.time()

def on_quote_push(symbol: str, event: PushQuote):
    """WebSocket 毫秒级回调，侦测极端异动"""
    try:
        clean_symbol = symbol.split('.')[0] if '.' in symbol else symbol
        
        # 🚨 核心风控白名单过滤：如果推送的个股既不在今日自选标的池，也不是持仓，直接忽略（防止历史订阅残留/脏事件触发）
        if clean_symbol not in app_config.WATCHLIST and clean_symbol not in _global_pos_map:
            return

        current_price = float(event.last_done)
        
        # 读取持仓成本
        cost_price = None
        if clean_symbol in _global_pos_map:
            cost_price = float(_global_pos_map[clean_symbol].get("cost_price", 0))
            
        # 1. 致命跌破熔断: 瞬间跌破成本 8% (防崩盘)
        if cost_price and current_price < cost_price * 0.92:
            if not _is_cooldown(clean_symbol, "DEFENSIVE_DROP_WS"):
                with _ws_lock:
                    if not any(e["symbol"] == clean_symbol and "DEFENSIVE_DROP" in e["type"] for e in _ws_events):
                        _ws_events.append({
                            "symbol": clean_symbol,
                            "type": "DEFENSIVE_DROP_WS",
                            "reason": f"[WS毫秒级拦截] 最新价 ${current_price:.2f} 瞬间跌破持仓成本 ${cost_price:.2f} 的 8%，系统极速熔断报警！"
                        })
        
        # 2. 毫秒级动能突破: 突破日内最高点 (抢跑)
        day_high = float(event.high)
        if day_high > 0 and current_price >= day_high * 0.998:
            if not _is_cooldown(clean_symbol, "OFFENSIVE_BREAKOUT_WS"):
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
            ctx.subscribe(full_symbols, [SubType.Quote])
            logger.info(f"⚡ [WebSocket] 已成功订阅 {len(full_symbols)} 只标的的毫秒级深度行情！")
            
            # 初始化记录
            global _last_monitored_symbols
            _last_monitored_symbols = set(app_config.WATCHLIST)
    except Exception as e:
        logger.error(f"[WebSocket] 订阅失败: {e}")


def update_websocket_subscriptions(logger, new_watchlist=None):
    """
    动态更新 WebSocket 订阅，保持订阅范围为: [当前持仓 + 当前自选股(Watchlist)]
    自动退订不再需要的个股，订阅新增的个股。
    """
    try:
        from longport.openapi import SubType
        import config as app_config
        
        ctx = get_quote_ctx()
        
        # 确定当前的自选股
        watchlist = new_watchlist if new_watchlist is not None else app_config.WATCHLIST
        
        # 确定当前的持仓
        global _global_pos_map
        pos_symbols = list(_global_pos_map.keys()) if _global_pos_map else []
        
        # 转换为规范的带 .US 后缀的代码
        target_symbols = set()
        for sym in (pos_symbols + watchlist):
            if sym:
                target_symbols.add(modify_symbol(sym))
                
        # 获取当前的所有活跃订阅
        current_subs = ctx.subscriptions()
        current_subscribed_symbols = set()
        for sub in current_subs:
            # 仅处理 SubType.Quote 类型的订阅
            if any(str(st) == "SubType.Quote" or "Quote" in str(st) for st in sub.sub_types):
                current_subscribed_symbols.add(sub.symbol)
                
        # 需要退订的: 当前订阅中存在，但不在目标集合中的
        to_unsubscribe = list(current_subscribed_symbols - target_symbols)
        # 需要新增订阅的: 目标集合中存在，但当前未订阅的
        to_subscribe = list(target_symbols - current_subscribed_symbols)
        
        if to_unsubscribe:
            ctx.unsubscribe(to_unsubscribe, [SubType.Quote])
            logger.info(f"⚡ [WebSocket] 自动退订无用标的: {to_unsubscribe}")
            
        if to_subscribe:
            ctx.subscribe(to_subscribe, [SubType.Quote])
            logger.info(f"⚡ [WebSocket] 自动追加新标的订阅: {to_subscribe}")
            
        logger.info(f"⚡ [WebSocket] 订阅同步完毕，当前共订阅 {len(target_symbols)} 只活跃标的行情。")
        
    except Exception as e:
        logger.error(f"[WebSocket] 动态更新订阅失败: {e}")


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
        
        # 🚨 动态增量更新订阅：如果当前持仓或自选股发生变动，自动同步 WebSocket 订阅
        global _last_monitored_symbols
        if monitor_symbols != _last_monitored_symbols:
            logger.info(f"🔄 WebSocket 侦测到监控范围变动！旧范围: {list(_last_monitored_symbols)} -> 新范围: {list(monitor_symbols)}")
            _last_monitored_symbols = monitor_symbols
            update_websocket_subscriptions(logger)
        
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
                
                # 获取动态波动率 ATR (缓存或实时计算，此处简化为每次巡检获取，因请求频率低可接受)
                atr = _get_daily_atr(quote_ctx, full_symbol, 14)
                
                if atr:
                    # 动态阈值：宽容防守 = 跌破成本 2.0 倍 ATR (防洗盘)，锁润 = 盈利超 3 倍 ATR
                    stop_loss_price = cost - 2.0 * atr
                    take_profit_price = cost + 3.0 * atr
                    
                    # 1.1 动态防守：跌破波动率安全垫
                    if current_price < stop_loss_price:
                        events.append({
                            "symbol": symbol,
                            "type": "DEFENSIVE_DROP",
                            "reason": f"最新价 ${current_price:.2f} 已跌破动态防守线 ${stop_loss_price:.2f} (持仓成本 - 2.0 * ATR)，趋势可能严重逆转，需要紧急评估止损！"
                        })
                    # 1.2 动态锁润：暴涨偏离合理波动区间
                    elif current_price > take_profit_price and rsi_15 > 80:
                        events.append({
                            "symbol": symbol,
                            "type": "SWING_EXHAUSTION",
                            "reason": f"最新价 ${current_price:.2f} 已达到动态止盈线 ${take_profit_price:.2f} (持仓成本 + 3 * ATR)，且 15分钟RSI({rsi_15:.1f})极度超买，建议逢高卖出部分做T锁定利润。"
                        })
                else:
                    # 兜底：如果获取不到 ATR，退回静态百分比
                    if current_price < cost * 0.92:
                        events.append({
                            "symbol": symbol,
                            "type": "DEFENSIVE_DROP",
                            "reason": f"最新价 ${current_price:.2f} 已跌破持仓成本 ${cost:.2f} 的 8%，需要紧急评估止损 ！"
                        })
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
                    
        # 过滤并设置冷却期
        filtered_events = []
        for e in events:
            if not _is_cooldown(e["symbol"], e["type"]):
                filtered_events.append(e)
                _set_cooldown(e["symbol"], e["type"])
        events = filtered_events
                    
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
    logger.info("LP-Agent v4.2 (Watchdog + Strategic Brain) 启动")
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
        app_secret=config.feishu.app_secret,
        chat_id=config.feishu.chat_id
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
    
    # 盘前选股自适应补跑检查
    try:
        import os
        import json
        import config as app_config
        current_time_startup = datetime.now(eastern)
        current_date_startup = current_time_startup.strftime('%Y-%m-%d')
        watchlist_path = "data/daily_watchlist.json"
        need_scan = False
        
        if os.path.exists(watchlist_path):
            with open(watchlist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") != current_date_startup:
                need_scan = True
        else:
            need_scan = True
            
        if need_scan and current_time_startup.strftime("%H:%M") >= "09:00":
            logger.info("⚠️ 侦测到盘前自选股已过期且已过 09:00 AM，启动自适应补跑 Alpha Scanner...")
            from agent.alpha_scanner import AlphaScanner
            scanner = AlphaScanner(llm=analyst_llm)
            new_watchlist, scan_reason = scanner.run()
            
            app_config.update_watchlist_in_place(new_watchlist)
            logger.info(f"✅ 自适应补跑 Alpha Scanner 完毕，标的池已就地更新为: {app_config.WATCHLIST}")
            scanner_ran = True
            
            if feishu_notifier:
                feishu_notifier.send_card(
                    title="🌅 每日动态标的池自动补跑更新",
                    content=f"**补跑原因：** 守护进程重启/过期自适应修复\n\n**今日监控名单：**\n`{', '.join(new_watchlist)}`",
                    color="turquoise"
                )
    except Exception as e:
        logger.error(f"启动自适应补跑 Alpha Scanner 失败: {e}")
    
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
                # 清空盘前的无效 WebSocket 异动
                with _ws_lock:
                    global _ws_events
                    _ws_events = []
                    
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

    logger.info("LP-Agent v4.2 已退出")

if __name__ == "__main__":
    main()
