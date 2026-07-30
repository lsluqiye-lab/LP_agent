
import asyncio
import json
import sys
import os

# 路径修复
sys.path.append(os.getcwd())

from agent.react import ReActAgent
from llm.gemini import GeminiLLM
from tools.base import ToolRegistry

async def test_v462_awareness():
    print("🚀 开始验证 v4.6.2 融资感知与交易频率反馈测试...")
    
    from config import LLMConfig
    gemini_cfg = LLMConfig.from_env("gemini")
    llm = GeminiLLM(
        api_key=gemini_cfg.api_key,
        model=gemini_cfg.model
    )
    
    # 模拟融资状态 (Cash < 0) 和 高频交易 (55/8)
    pre_executed_data = {
        "account": {
            "cash": -433043.30,
            "buying_power": 1399907.48,
            "net_assets": 1831675.09
        },
        "positions": []
    }
    
    mock_briefing = json.dumps([{
        "symbol": "AAPL",
        "verdict": "BUY",
        "reason": "Technical breakout.",
        "quant_metadata": {"score": 82, "conviction": "normal", "rank": 5}
    }])

    agent = ReActAgent(llm, ToolRegistry())
    
    print("\n--- [CIO Reasoning Test with Financing & High Frequency] ---")
    # 注意：ReActAgent.run 内部会从 trade_logger 获取今日交易次数
    # 为了测试，我们可能需要 mock trade_logger 或者直接检查生成的 prompt
    
    # 我们可以通过拦截 messages 来检查 prompt
    decision = await agent.run(
        decision_briefings_json=mock_briefing,
        risk_context="Market is NORMAL",
        risk_score=70.0,
        pre_executed_data=pre_executed_data
    )
    
    print("\n--- [CIO Final Decision] ---")
    print(decision)
    
    # 验证逻辑点：
    # 1. CIO 是否意识到目前在融资 (Cash < 0)
    # 2. CIO 是否提到交易频率过高或需要合并决策
    # 3. CIO 是否因为 Alpha 分数不够高 (82 < 85/90) 而拒绝买入
    
    decision_lower = decision.lower()
    keywords = ["融资", "借贷", "利息", "现金", "频率", "次数", "微调", "损耗", "账单"]
    found_keywords = [k for k in keywords if k in decision]
    
    print(f"\n找到的关键词: {found_keywords}")
    
    if len(found_keywords) >= 2:
        print("\n✅ 测试通过: CIO 成功感知到了融资状态与交易频率。")
    else:
        print("\n❌ 测试失败: CIO 未能表现出足够的资金与频率意识。")

if __name__ == "__main__":
    asyncio.run(test_v462_awareness())
