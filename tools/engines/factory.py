# tools/engines/factory.py
import os
import logging
from tools.engines.base import BaseTradingEngine

_trading_engine_instance = None

def get_trading_engine() -> BaseTradingEngine:
    """
    核心单例工厂：根据环境配置动态加载底层券商执行通道
    """
    global _trading_engine_instance
    if _trading_engine_instance is None:
        trading_provider = os.getenv("TRADING_PROVIDER", "longport").lower()
        logging.info(f"🔌 [Engine Factory] Loading Pluggable Trading Provider: [{trading_provider}]")
        
        if trading_provider == "longport":
            from tools.engines.longport_engine import LongPortTradingEngine
            _trading_engine_instance = LongPortTradingEngine()
        elif trading_provider == "usmart":
            # 预留给未来对接的盈立证券 (uSMART)
            # from tools.engines.usmart_engine import USMARTTradingEngine
            # _trading_engine_instance = USMARTTradingEngine()
            raise NotImplementedError("uSMART 交易引擎已在路线图中规划，但尚未完成正式对接。请在 .env 中将 TRADING_PROVIDER 设为 'longport' 进行测试。")
        else:
            raise ValueError(f"❌ Unsupported trading provider: {trading_provider}")
            
    return _trading_engine_instance
