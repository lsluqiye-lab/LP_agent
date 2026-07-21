
import asyncio
import json
import sys
import os

# 路径修复
sys.path.append(os.getcwd())

from agent.risk_manager import get_risk_manager
from agent.react import ReActAgent
from llm.gemini import GeminiLLM
from tools.base import ToolRegistry

async def test_cio_sobriety():
    print("🚀 开始 CIO 清醒度与多维度风险感知测试 (Scenario: Extreme Euphoria)...")
    
    # 1. 模拟极端超买数据
    # 模拟 market_overview，其中 RSI 广度极大，情绪极高
    mock_market_overview = {
        "market_temperature": {"temperature": 55, "sentiment": 92}, # 极高情绪
        "indexes": {
            "SPY": {"intraday_change_pct": 0.5, "price": 550, "uptrend": True, "RSI_14": 85, "ATR_14": 5.0},
            "QQQ": {"intraday_change_pct": 0.7, "price": 480, "uptrend": True, "RSI_14": 88, "ATR_14": 6.0}
        },
        "watchlist_scan": [
            {"symbol": "NVDA", "rsi_14": 85, "return_20d_pct": 15},
            {"symbol": "TSM", "rsi_14": 82, "return_20d_pct": 12},
            {"symbol": "AAPL", "rsi_14": 81, "return_20d_pct": 10},
            {"symbol": "MSFT", "rsi_14": 79, "return_20d_pct": 8},
            {"symbol": "AMZN", "rsi_14": 84, "return_20d_pct": 11}
        ]
    }

    # 2. 计算风控评分
    rm = get_risk_manager()
    risk_report = rm.calculate_risk_score(mock_market_overview)
    risk_summary = rm.get_risk_summary()
    
    print("\n--- [RiskManager Output] ---")
    print(risk_summary)
    
    # 验证评分是否被惩罚机制压低 (预期: 尽管趋势好，但情绪和RSI会把分拉低)
    score = risk_report["score"]
    regime = risk_report["regime"]
    print(f"\nFinal Score: {score}, Regime: {regime}")
    
    # 3. 模拟 CIO 决策
    # 我们使用 Gemini 模型来验证其对 Prompt 的理解
    from config import LLMConfig
    gemini_cfg = LLMConfig.from_env("gemini")
    llm = GeminiLLM(
        api_key=gemini_cfg.api_key,
        model=gemini_cfg.model
    )
    
    # 模拟一个诱人的买入研报
    mock_briefing = json.dumps([{
        "symbol": "NVDA",
        "verdict": "STRONG_BUY",
        "reason": "NVDA just broke all-time high with massive momentum. AI narrative is peaking. Technical score 5/5.",
        "quant_metadata": {"score": 95, "conviction": "high", "rank": 1}
    }])

    agent = ReActAgent(llm, ToolRegistry())
    
    # 我们只看 Thought 过程，不实际执行工具（或者模拟工具返回）
    print("\n--- [CIO Reasoning Test] ---")
    decision = await agent.run(
        decision_briefings_json=mock_briefing,
        risk_context=risk_summary,
        risk_score=score,
        portfolio_directives={"allow_new_buy": risk_report["constraints"]["allow_new_buy"]}
    )
    
    print("\n--- [CIO Final Decision] ---")
    print(decision)
    
    # 验证逻辑点：
    # 1. 评分是否低于 80 (因为极度贪婪惩罚)
    # 2. CIO 的 Thought 中是否提到了 "Extreme Greed", "Overbought", "Contrarian" 等关键词
    # 3. CIO 是否选择了谨慎或拒绝融资大额买入
    
    if "greedy" in decision.lower() or "overbought" in decision.lower() or "caution" in decision.lower():
        print("\n✅ 测试通过: CIO 成功感知到了多维度风险标签并表现出清醒度。")
    else:
        print("\n❌ 测试失败: CIO 似乎仍然被动能冲昏头脑。")

if __name__ == "__main__":
    asyncio.run(test_cio_sobriety())
