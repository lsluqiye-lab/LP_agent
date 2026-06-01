# tools/engines/base.py
import re
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List

class BaseTradingEngine(ABC):
    """
    LP-Agent 统一交易引擎抽象接口 (Broker-Agnostic Interface)
    所有具体券商适配器 (LongPort, uSMART, IBKR) 都必须继承并实现此基类。
    """

    @abstractmethod
    def submit_order(
        self,
        symbol: str,             # 股票或期权代码 (如 TSM, TSM260619P00150000.US)
        side: str,               # "Buy" 或 "Sell"
        order_type: str,         # "LO" (限价), "MO" (市价), "TSM" (追踪止损), "LIT" (限价触及), "MIT" (市价触及)
        quantity: float,         # 正股股数或期权张数（底仓系统通常使用股数，适配器应自动处理乘数）
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        trailing_percent: Optional[float] = None,
        trailing_amount: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """提交订单：提交买入/卖出限价或条件单，返回包含 order_id 的标准字典"""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤销挂单"""
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """
        获取当前账户持仓。
        统一规范返回字段格式：
        [
            {
                "symbol": "TSM",
                "quantity": 100.0,
                "cost_price": 150.2,
                "market_value": 16200.0,
                "asset_type": "STOCK" # "STOCK" 或 "OPTION"
            }
        ]
        """
        pass

    @abstractmethod
    def get_account_balance(self) -> Dict[str, Any]:
        """获取账户资金余额、可用购买力与总资产"""
        pass

    @abstractmethod
    def get_today_orders(self) -> List[Dict[str, Any]]:
        """获取今日订单列表并统一其状态字段"""
        pass

    @abstractmethod
    def get_history_orders(self, days: int) -> List[Dict[str, Any]]:
        """获取历史订单记录"""
        pass

    @staticmethod
    def is_option_symbol(symbol: str) -> bool:
        """
        通过正则判断代码是否符合 OCC 标准美股期权格式
        例如: TSM260619P00150000.US 或 TSM260619P00150000
        """
        # 移除后缀
        clean_sym = symbol.split('.')[0] if '.' in symbol else symbol
        # OCC 标准格式: 字母(1-6位) + 6位数字(到期日) + C/P(方向) + 8位数字(行权价)
        pattern = r'^[A-Z]{1,6}[0-9]{6}[CP][0-9]{8}$'
        return bool(re.match(pattern, clean_sym))
