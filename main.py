"""
LP-Agent 主入口
AI自动交易智能体
"""
import sys
import time
from datetime import datetime, time as dt_time

import pytz
import holidays

from config import AppConfig
from logger import setup_logger
from llm.deepseek import DeepSeekLLM
from llm.gemini import GeminiLLM
from tools.base import ToolRegistry
from tools.trading import create_trading_tools
from tools.search import create_search_tools
from tools.market_data import create_market_data_tools
from agent.react import ReActAgent, TRADING_SYSTEM_PROMPT
from notification.feishu import FeishuNotifier


def is_trading_hours(eastern_time: datetime) -> bool:
    """
    检查是否在交易时段（盘前1小时 + 盘中 + 盘后1小时）

    Args:
        eastern_time: 美东时间

    Returns:
        是否在交易时段
    """
    # 检查假日
    nyse_holidays = holidays.NYSE()
    today_str = eastern_time.strftime('%Y-%m-%d')
    if nyse_holidays.get(today_str):
        return False

    # 检查周末
    if eastern_time.weekday() >= 5:
        return False

    current_t = eastern_time.time()

    # 盘中 9:30 - 16:00
    # 盘前1小时 8:30 - 9:30
    # 盘后1小时 16:00 - 17:00
    if dt_time(8, 30) <= current_t <= dt_time(17, 0):
        return True

    return False


def get_sleep_interval(config: AppConfig, eastern_time: datetime) -> int:
    """
    获取休眠间隔

    Args:
        config: 应用配置
        eastern_time: 美东时间

    Returns:
        休眠秒数
    """
    if is_trading_hours(eastern_time):
        return config.agent.sleep_interval_trading
    return config.agent.sleep_interval_non_trading


def create_llm(config: AppConfig):
    """
    根据配置创建LLM实例

    Args:
        config: 应用配置

    Returns:
        LLM实例
    """
    provider = config.llm.provider

    if provider == "deepseek":
        return DeepSeekLLM(
            api_key=config.llm.api_key,
            base_url=config.llm.base_url,
            model=config.llm.model,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
    elif provider == "gemini":
        # 主LLM不启用搜索，搜索功能由 tools/search.py 的搜索工具独立实现
        return GeminiLLM(
            api_key=config.llm.api_key,
            model=config.llm.model,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
    # 未来可以在这里继续扩展，例如：
    # elif provider == "openai":
    #     return OpenAILLM(...)
    else:
        raise ValueError(f"不支持的LLM提供商: {provider}")


def main():
    """主函数"""
    # 加载配置
    # 可以通过环境变量 LLM_PROVIDER 切换模型提供商
    import os
    llm_provider = os.getenv("LLM_PROVIDER", "deepseek")
    config = AppConfig.from_env(llm_provider)

    # 将LongPort配置写入环境变量
    config.longport.to_env()

    # 设置日志
    logger = setup_logger("trading_agent", config.log)
    logger.info("=" * 60)
    logger.info("LP-Agent 启动")
    logger.info(f"LLM提供商: {config.llm.provider}")
    logger.info(f"模型: {config.llm.model}")
    logger.info("=" * 60)

    # 验证配置
    if not config.llm.api_key:
        logger.error(f"未配置 {config.llm.provider.upper()}_API_KEY 环境变量")
        sys.exit(1)

    # 创建LLM
    try:
        llm = create_llm(config)
        logger.info(f"LLM初始化成功: {llm.get_provider_name()}")
    except Exception as e:
        logger.error(f"LLM初始化失败: {e}")
        sys.exit(1)

    # 创建工具注册表
    tool_registry = ToolRegistry()

    # 注册交易工具
    trading_tools = create_trading_tools()
    tool_registry.register_all(trading_tools)
    logger.info(f"已注册 {len(trading_tools)} 个交易工具")

    # 注册行情数据工具（技术分析、K线、资金流向等）
    try:
        market_data_tools = create_market_data_tools()
        tool_registry.register_all(market_data_tools)
        logger.info(f"已注册 {len(market_data_tools)} 个行情数据工具")
    except Exception as e:
        logger.warning(f"行情数据工具初始化失败: {e}")

    # 注册搜索工具（使用Gemini + Google Search）
    try:
        search_tools = create_search_tools()
        tool_registry.register_all(search_tools)
        logger.info(f"已注册 {len(search_tools)} 个搜索工具")
    except Exception as e:
        logger.warning(f"搜索工具初始化失败（可能GEMINI_API_KEY未设置）: {e}")

    logger.info(f"工具总数: {len(tool_registry)}")

    # 初始化飞书通知器（可选）
    feishu_notifier = None
    if config.feishu.enabled:
        try:
            feishu_notifier = FeishuNotifier(webhook_url=config.feishu.webhook_url)
            logger.info("飞书通知器初始化成功")
        except Exception as e:
            logger.warning(f"飞书通知器初始化失败: {e}")
    else:
        logger.info("飞书通知未配置，跳过")

    # 创建智能体
    agent = ReActAgent(
        llm=llm,
        tool_registry=tool_registry,
        system_prompt=TRADING_SYSTEM_PROMPT,
        max_iterations=config.agent.max_iterations,
        pre_run_tools=[
            "get_market_status",
            "get_positions",
            "get_account_balance",
            "get_today_orders",
            "get_history_orders",
            "search_macro_economics",
            "search_earnings_calendar",
            "search_geopolitical_news",
        ],
        feishu_notifier=feishu_notifier,
        logger=logger
    )
    logger.info("ReAct智能体初始化完成")

    # 主循环
    eastern = pytz.timezone('US/Eastern')
    beijing = pytz.timezone('Asia/Shanghai')

    while True:
        try:
            current_time = datetime.now(eastern)
            beijing_time = datetime.now(beijing)
            logger.info("=" * 50)
            logger.info(f"开始新一轮执行 - 美东: {current_time.strftime('%Y-%m-%d %H:%M:%S')} | 北京: {beijing_time.strftime('%Y-%m-%d %H:%M:%S')}")

            # 检查是否在交易时段
            if not is_trading_hours(current_time):
                logger.info("当前不在交易时段，跳过执行")
            else:
                # 运行智能体
                logger.info("开始执行智能体...")
                result = agent.run()
                logger.info(f"智能体执行完成")
                logger.info(f"执行结果:\n{result}")

            # 计算休眠时间
            sleep_seconds = get_sleep_interval(config, current_time)
            logger.info(f"休眠 {sleep_seconds} 秒（下次执行约 北京: {datetime.now(beijing).strftime('%H:%M:%S')} 之后）")
            logger.info("=" * 50)

            time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            logger.info("收到中断信号，正在退出...")
            break
        except Exception as e:
            logger.error(f"主循环出错: {e}", exc_info=True)
            # 出错后休眠一段时间再继续
            time.sleep(60)

    logger.info("LP-Agent 已退出")


if __name__ == "__main__":
    main()
