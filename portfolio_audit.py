"""
LP-Agent 持仓深度审计工具 (Analysis Only)
针对满仓+融资账户的专项诊断
"""
import asyncio
import json
import logging
import os
from datetime import datetime
import pytz

from config import AppConfig, WATCHLIST
from logger import setup_logger
from agent.orchestrator import ExpertOrchestrator
from agent.risk_manager import get_risk_manager
from tools.trading import GetPositionsTool, GetAccountBalanceTool
from tools.market_data import GetMarketOverviewTool
from llm.gemini import GeminiLLM
from llm.deepseek import DeepSeekLLM

async def audit_portfolio():
    # 1. 初始化
    os.environ["LLM_PROVIDER"] = "gemini" # 建议用 Gemini 分析，理解力强
    config = AppConfig.from_env("gemini")
    config.longport.to_env()
    logger = setup_logger("portfolio_audit", config.log)
    
    logger.info("="*60)
    logger.info("🚀 开始实盘持仓深度诊断 (Analysis Only Mode)")
    logger.info("="*60)

    try:
        primary_llm = GeminiLLM(api_key=config.llm.api_key, model=config.llm.model)
        orchestrator = ExpertOrchestrator(llm=primary_llm)
        
        # 2. 获取账户现状
        pos_tool = GetPositionsTool()
        balance_tool = GetAccountBalanceTool()
        market_tool = GetMarketOverviewTool()
        
        logger.info("正在获取账户与大盘数据...")
        positions_raw = pos_tool.execute()
        balance_raw = balance_tool.execute()
        market_raw = market_tool.execute()
        
        positions = json.loads(positions_raw).get("positions", [])
        balance = json.loads(balance_raw)
        market_data = json.loads(market_raw)
        
        net_assets = float(balance.get("net_assets", 0))
        total_cash = float(balance.get("total_cash", 0))
        
        logger.info(f"账户净资产: ${net_assets:,.2f} | 可用现金: ${total_cash:,.2f}")
        if total_cash < 0:
            logger.warning(f"⚠️ 检测到融资欠款: ${abs(total_cash):,.2f}")

        # 3. 计算宏观风险
        risk_manager = get_risk_manager(config.risk)
        risk_input = {
            "indexes": market_data.get("indexes", {}),
            "market_temperature": market_data.get("market_temperature", {})
        }
        risk_result = risk_manager.calculate_risk_score(risk_input, {"current_net_assets": net_assets, "daily_start_assets": net_assets})
        
        logger.info(f"大盘风控评分: {risk_result['score']} ({risk_result['regime']})")
        logger.info(f"风控建议: {risk_result['constraints']['message']}")

        # 4. 逐一诊断持仓
        audit_results = []
        macro_briefing = {
            "risk_level": risk_result["regime"].upper(),
            "score": risk_result["score"],
            "summary": risk_result["constraints"]["message"]
        }

        logger.info(f"开始对 {len(positions)} 只持仓标的进行专家会诊...")
        
        for pos in positions:
            symbol = pos["symbol"]
            qty = pos["quantity"]
            cost = pos["cost_price"]
            mkt_val = float(pos["market_value"])
            weight = (mkt_val / net_assets) * 100
            
            logger.info(f"分析 {symbol} (权重: {weight:.1f}%) ...")
            
            # 调用专家团
            briefing = await orchestrator.get_full_briefing(symbol, macro_briefing)
            
            # 简单的风险判定逻辑
            tech = briefing.get("technical", {})
            stage = tech.get("trend_stage", "Unknown")
            is_risky = stage in ["Stage 4", "Unknown"] or tech.get("price_above_SMA200") is False
            
            audit_results.append({
                "symbol": symbol,
                "weight": f"{weight:.1f}%",
                "stage": stage,
                "verdict": "⚠️ 建议减仓/清仓" if is_risky else "✅ 暂时持有",
                "conflicts": briefing.get("identified_conflicts", []),
                "tech_summary": tech.get("summary", ""),
                "fund_summary": briefing.get("fundamental", {}).get("summary", "")
            })

        # 5. 输出报告
        print("\n" + "="*80)
        print(f"📊 LP-Agent 持仓诊断报告 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        print("="*80)
        print(f"账户状态: {'🚨 融资运行' if total_cash < 0 else '正常'}")
        print(f"风控环境: {risk_result['regime'].upper()} ({risk_result['score']}/100)")
        print("-" * 80)
        print(f"{'代码':<8} | {'权重':<6} | {'趋势阶段':<10} | {'操作建议'}")
        print("-" * 80)
        for r in audit_results:
            print(f"{r['symbol']:<8} | {r['weight']:<6} | {r['stage']:<10} | {r['verdict']}")
        
        print("\n💡 深度分析:")
        for r in audit_results:
            if "建议" in r["verdict"]:
                print(f"--- {r['symbol']} ---")
                print(f"原因: {r['tech_summary']}")
                if r['conflicts']:
                    print(f"矛盾点: {', '.join(r['conflicts'])}")

    except Exception as e:
        logger.error(f"审计过程中出错: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(audit_portfolio())
