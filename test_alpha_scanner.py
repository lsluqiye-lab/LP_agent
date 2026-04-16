import asyncio
import os
import sys
import logging

# 配置日志输出到终端
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# 确保能导入根目录下的模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.alpha_scanner import AlphaScanner
from llm.base import BaseLLM
from config import AppConfig

# 初始化配置
app_config = AppConfig.from_env(os.getenv("LLM_PROVIDER", "gemini"))

async def test_alpha_scanner():
    print("=== 开始测试 Alpha Scanner ===")
    
    # 动态加载 LLM
    analyst_llm = None
    llm_conf = app_config.analyst_llm or app_config.llm
    if llm_conf.provider == "deepseek":
        from llm.deepseek import DeepSeekLLM
        analyst_llm = DeepSeekLLM(
            api_key=llm_conf.api_key,
            model=llm_conf.model,
            base_url=llm_conf.base_url
        )
    elif llm_conf.provider == "gemini":
        from llm.gemini import GeminiLLM
        analyst_llm = GeminiLLM(
            api_key=llm_conf.api_key,
            model=llm_conf.model,
            fallback_model=llm_conf.fallback_model
        )
    
    scanner = AlphaScanner(llm=analyst_llm)
    
    # 运行选股器
    print("1. 正在获取当前持仓...")
    holdings = scanner._get_current_holdings()
    print(f"当前持仓: {holdings}")
    
    print("\n2. 正在执行选股分析...")
    print("这可能需要一两分钟时间，请稍候...")
    watchlist = scanner.run()
    
    print("\n=== 测试完成 ===")
    print(f"最终选出的股票池 (包含持仓): {watchlist}")

if __name__ == "__main__":
    asyncio.run(test_alpha_scanner())
