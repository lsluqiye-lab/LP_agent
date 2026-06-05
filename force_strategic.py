import asyncio
import logging
from config import AppConfig
from main import run_strategic_cycle, setup_logger, create_llm, create_market_data_tools, create_search_tools, create_trading_tools
from tools.base import ToolRegistry
from agent.orchestrator import ExpertOrchestrator
from agent.react import ReActAgent
from agent.react import STRATEGIC_SYSTEM_PROMPT
from data.trade_logger import get_trade_logger
import os
from notification.feishu import FeishuNotifier

config = AppConfig.from_env(os.getenv("LLM_PROVIDER", "gemini"))
logger = setup_logger("force_strategic")

tool_registry = ToolRegistry()
tool_registry.register_all(create_trading_tools())
tool_registry.register_all(create_market_data_tools())
tool_registry.register_all(create_search_tools())

primary_llm = create_llm(config.llm)
analyst_llm = create_llm(config.analyst_llm) if config.analyst_llm else primary_llm

feishu_notifier = FeishuNotifier(webhook_url=config.feishu.webhook_url, app_id=config.feishu.app_id, app_secret=config.feishu.app_secret) if config.feishu.enabled else None
orchestrator = ExpertOrchestrator(llm=analyst_llm, feishu_notifier=feishu_notifier)

agent = ReActAgent(llm=primary_llm, tool_registry=tool_registry, system_prompt=STRATEGIC_SYSTEM_PROMPT, max_iterations=5, feishu_notifier=feishu_notifier, logger=logger)
trade_logger = get_trade_logger()

print("🚀 正在强制执行早盘扫描与深度决策层 (Strategic Cycle) ...")
asyncio.run(run_strategic_cycle(tool_registry, config, logger, orchestrator, agent, feishu_notifier, False, trade_logger))
print("✅ 深度决策层强制执行完毕！")
