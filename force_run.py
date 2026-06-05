import asyncio
from datetime import datetime
import pytz
import logging
from config import AppConfig
from main import run_strategic_cycle, setup_logger, create_llm, create_market_data_tools, create_search_tools, create_trading_tools
from tools.base import ToolRegistry
from agent.orchestrator import ExpertOrchestrator
from agent.react import ReActAgent, STRATEGIC_SYSTEM_PROMPT
import os
from data.trade_logger import get_trade_logger
from notification.feishu import FeishuNotifier

config = AppConfig.from_env(os.getenv("LLM_PROVIDER", "gemini"))
logger = setup_logger("force_run")

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

# 1. Force Scanner
from agent.alpha_scanner import AlphaScanner
scanner = AlphaScanner(llm=analyst_llm)
logger.info("============== 强制触发盘前选股 (Alpha Scanner) ==============")
new_watchlist, scan_reason = scanner.run()
import config as app_config
app_config.update_watchlist_in_place(new_watchlist)

if feishu_notifier:
    feishu_notifier.send_card(
        title="🌅 强制刷新动态标的池",
        content=f"**选股逻辑：**\n{scan_reason}\n\n**今日监控名单：**\n`{', '.join(new_watchlist)}`",
        color="purple"
    )

# 2. Force Strategic Brain
logger.info("============== 强制触发深度决策脑 (Strategic Cycle) ==============")
asyncio.run(run_strategic_cycle(tool_registry, config, logger, orchestrator, agent, feishu_notifier, False, trade_logger))
print("✅ 全部强制执行流程结束！请查看飞书通知。")
