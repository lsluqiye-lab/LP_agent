# tools/engines/__init__.py
"""
交易引擎适配器包
支持多券商抽象，物理上将行情数据查询与交易执行通道彻底分离。
"""
from tools.engines.base import BaseTradingEngine
from tools.engines.factory import get_trading_engine
