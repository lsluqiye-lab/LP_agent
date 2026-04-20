import asyncio
import os
import json
from config import AppConfig
from logger import setup_logger
from llm.gemini import GeminiLLM
from tools.base import ToolRegistry
from tools.market_data import create_market_data_tools
from tools.search import create_search_tools
from agent.orchestrator import ExpertOrchestrator
from agent.schemas import MacroBriefing

async def test_tsla_analysis():
    # 1. 模拟宏观环境 (假设当前是偏乐观的 NORMAL 状态)
    macro_briefing: MacroBriefing = {
        "risk_level": "NORMAL",
        "score": 65,
        "summary": "大盘趋势良好，资金温和流入，适合针对特定个股进行建仓或持股。",
        "key_events": []
    }

    # 2. 初始化 LLM 与工具
    config = AppConfig.from_env("gemini")
    llm = GeminiLLM(
        api_key=config.llm.api_key,
        model=config.llm.model,
        temperature=0.1
    )
    
    tool_registry = ToolRegistry()
    tool_registry.register_all(create_market_data_tools())
    tool_registry.register_all(create_search_tools())
    
    # 注入工具依赖 (如果是类工具可以直接使用)
    orchestrator = ExpertOrchestrator(llm)

    print("==================================================")
    print("🚀 启动 TSLA 单票深度全景测试 (包含量价/消息面)")
    print("==================================================\n")
    
    symbol = "TSLA"

    try:
        print("正在获取全局板块分析...")
        sector_briefing = await orchestrator.get_sector_briefing()
        print(f"板块分析: {sector_briefing.get('summary')}")

        # 并发跑三个专家的分析
        print(f"正在呼叫三位专家分析 {symbol} (这可能需要20-30秒)...")
        briefing = await orchestrator.get_full_briefing(symbol, macro_briefing, sector_briefing)        
        print("\n✅ 分析完成！以下是专家简报结果：\n")
        print(json.dumps(briefing, indent=2, ensure_ascii=False))
        
        print("\n\n" + "="*50)
        print("💡 验证重点：")
        print("1. [技术面] - 是否提取了 support/resistance_levels？volume_price_analysis 是否关注了放量/缩量？")
        print("2. [消息/基本面] - 是否捕捉到了 'A15', 'FSD', 或者 'RoboTaxi' 等近期强催化剂词汇？")
        print("3. [决策流] - 是否标记出了 detected conflicts (专家意见分歧)？")
        print("="*50 + "\n")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")

if __name__ == "__main__":
    asyncio.run(test_tsla_analysis())