
import asyncio
import json
import logging
import os
from datetime import datetime
from dotenv import load_dotenv

from config import AppConfig, WATCHLIST
from logger import setup_logger
from main import create_llm
from agent.orchestrator import ExpertOrchestrator
from agent.react import ReActAgent
from agent.risk_manager import get_risk_manager
from tools.market_data import GetMarketOverviewTool
from tools.base import ToolRegistry
from tools.trading import create_trading_tools, GetPositionsTool, GetAccountBalanceTool

load_dotenv()

async def audit_portfolio():
    # 1. 初始化配置
    os.environ["LLM_PROVIDER"] = "gemini" 
    cfg = AppConfig.from_env(llm_provider="gemini")
    cfg.longport.to_env()
    
    # 设置日志
    logger = setup_logger("portfolio_audit", cfg.log)
    logger.info("="*60)
    logger.info("🚀 开始实盘持仓深度诊断 (Live Audit Mode)")
    logger.info("="*60)

    try:
        # 2. 初始化核心组件
        llm = create_llm(cfg.llm)
        orchestrator = ExpertOrchestrator(llm)
        risk_mgr = get_risk_manager(cfg.risk)
        
        pos_tool = GetPositionsTool()
        balance_tool = GetAccountBalanceTool()
        market_tool = GetMarketOverviewTool()

        # 3. 获取实时账户与大盘数据
        logger.info("正在获取实时账户与大盘数据...")
        positions_raw = pos_tool.execute()
        balance_raw = balance_tool.execute()
        market_raw = market_tool.execute()

        positions_data = json.loads(positions_raw).get("positions", [])
        balance_data = json.loads(balance_raw)
        market_data = json.loads(market_raw)

        net_assets = float(balance_data.get("net_assets", 0))
        total_cash = float(balance_data.get("total_cash", 0))
        
        logger.info(f"当前净资产: ${net_assets:,.2f} | 可用现金: ${total_cash:,.2f}")
        if total_cash < 0:
            logger.warning(f"⚠️ 账户存在融资负债: ${abs(total_cash):,.2f}")

        # 4. 计算宏观风险
        account_info = {
            "current_net_assets": net_assets,
            "daily_start_assets": net_assets # 审计模式下暂以当前为基准
        }
        risk_result = risk_mgr.calculate_risk_score(market_data, account_info)
        risk_context = risk_mgr.get_risk_summary()
        
        macro_briefing = {
            "risk_level": risk_result["regime"].upper(),
            "score": risk_result["score"],
            "summary": risk_result["constraints"]["message"],
            "key_events": []
        }
        logger.info(f"大盘风险等级: {macro_briefing['risk_level']} (评分: {macro_briefing['score']})")

        # 5. 逐一诊断持仓
        if not positions_data:
            logger.info("当前账户无持仓，无需诊断。")
            return

        logger.info(f"开始对 {len(positions_data)} 只持仓标的进行专家会诊...")
        briefings = []
        for pos in positions_data:
            symbol = pos["symbol"]
            
            # 容错处理：处理可能出现的 'N/A' 字符串
            def safe_float(val, default=0.0):
                try:
                    return float(val) if val and val != 'N/A' else default
                except (ValueError, TypeError):
                    return default

            mkt_val = safe_float(pos.get("market_value"))
            weight = (mkt_val / net_assets) * 100 if net_assets > 0 else 0
            
            logger.info(f"  🔍 分析 {symbol} (权重: {weight:.1f}%)...")
            try:
                briefing = await orchestrator.get_full_briefing(symbol, macro_briefing)
                briefings.append(briefing)
                # 频率限制保护
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"  ❌ 分析 {symbol} 失败: {e}")

        # 6. 构造 CIO 决策上下文
        logger.info("正在启动 CIO 综合审计决策...")
        tool_registry = ToolRegistry()
        for tool in create_trading_tools():
            tool_registry.register(tool)
            
        agent = ReActAgent(llm=llm, tool_registry=tool_registry, logger=logger)
        
        # 账户上下文，用于 CIO 判断
        account_context = {
            "net_assets": net_assets,
            "total_cash": total_cash,
            "positions": positions_data
        }

        decision = await agent.run(
            decision_briefings_json=json.dumps(briefings, ensure_ascii=False, indent=2),
            risk_context=risk_context,
            pre_executed_data=account_context
        )

        print("\n" + "="*80)
        print("📊 LP-Agent 实盘持仓深度审计报告 (CIO Decision)")
        print("="*80)
        print(decision)
        print("="*80)
        logger.info("审计任务圆满完成。")

    except Exception as e:
        logger.error(f"审计过程中出现严重错误: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(audit_portfolio())
