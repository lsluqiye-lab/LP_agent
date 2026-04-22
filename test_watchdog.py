import asyncio
import json
import logging
import os
from unittest.mock import MagicMock, patch

from config import AppConfig
from llm.gemini import GeminiLLM
from agent.react import ReActAgent
from tools.base import ToolRegistry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_watchdog")

async def test_watchdog_agent():
    print("==================================================")
    print("🚀 测试 Event-Driven Watchdog 雷达与 CIO 接管逻辑")
    print("==================================================\n")

    config = AppConfig.from_env("gemini")
    llm = GeminiLLM(
        api_key=config.llm.api_key,
        model=config.llm.model,
        temperature=0.1
    )
    
    # 我们只注册一个空壳工具用于测试 CIO 的 ReAct 推理
    tool_registry = ToolRegistry()
    agent = ReActAgent(llm, tool_registry)

    # 构造假数据
    interrupt_events = (
        "- [TSLA] DEFENSIVE_DROP: 最新价 $185.00 已跌破持仓成本 $205.00 的 8%，需要紧急评估止损！\n"
        "- [NVDA] OFFENSIVE_BREAKOUT: 股价($915.00)接近或突破日内高点($916.00)，15分钟RSI(75.5)强势，存在右侧动能爆发可能！"
    )

    decision_briefings_json = json.dumps({
        "TSLA": {
            "score": 30,
            "technical": "Downtrend",
            "fundamental": "Weak",
            "sentiment": "Bearish",
            "sector": "Neutral"
        },
        "NVDA": {
            "score": 85,
            "technical": "Strong Uptrend",
            "fundamental": "Strong",
            "sentiment": "Bullish",
            "sector": "Strong Bullish"
        }
    })

    print("🚨 模拟 Watchdog 生成的中断事件:\n", interrupt_events)
    print("\n🧠 唤醒 CIO 开始处理...\n")

    try:
        # 传递 interrupt_events 给 ReActAgent
        result = await agent.run(
            decision_briefings_json=decision_briefings_json,
            risk_context="Risk: NORMAL",
            risk_score=50.0,
            pre_executed_data={"account": "test"},
            interrupt_events=interrupt_events
        )
        print("\n✅ CIO 处理完毕！决策结果：\n")
        print(result)
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")

if __name__ == "__main__":
    asyncio.run(test_watchdog_agent())
