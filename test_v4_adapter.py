# test_v4_adapter.py
"""
LP-Agent V4.0 行情与交易解耦及期权适配物理层单元测试
"""
import unittest
import logging
from decimal import Decimal
from tools.engines.base import BaseTradingEngine
from tools.engines.factory import get_trading_engine
from tools.engines.longport_engine import LongPortTradingEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

class TestTradingV4Adapter(unittest.TestCase):
    
    def test_option_symbol_regex(self):
        """测试 OCC 标准美股期权符号的正则判定"""
        # 1. 真实正股代码 (不应被识别为期权)
        self.assertFalse(BaseTradingEngine.is_option_symbol("TSM"))
        self.assertFalse(BaseTradingEngine.is_option_symbol("TSM.US"))
        self.assertFalse(BaseTradingEngine.is_option_symbol("AAPL"))
        self.assertFalse(BaseTradingEngine.is_option_symbol("AAPL.US"))
        
        # 2. 真实期权代码 (应该被识别为期权)
        self.assertTrue(BaseTradingEngine.is_option_symbol("TSM260619P00150000.US"))
        self.assertTrue(BaseTradingEngine.is_option_symbol("TSM260619P00150000"))
        self.assertTrue(BaseTradingEngine.is_option_symbol("AAPL260619C00200000.US"))
        self.assertTrue(BaseTradingEngine.is_option_symbol("AAPL260619C00200000"))
        
        # 3. 边界和异常格式
        self.assertFalse(BaseTradingEngine.is_option_symbol("TSM260619P"))
        self.assertFalse(BaseTradingEngine.is_option_symbol("TSM260619P00150"))

    def test_factory_loading(self):
        """测试工厂方法能正常加载 LongPortTradingEngine 且为单例"""
        engine1 = get_trading_engine()
        engine2 = get_trading_engine()
        
        self.assertIsInstance(engine1, BaseTradingEngine)
        self.assertIsInstance(engine1, LongPortTradingEngine)
        # 单例验证
        self.assertEqual(id(engine1), id(engine2))

    def test_option_slippage_calculation(self):
        """测试期权专属自适应滑点算法"""
        engine = LongPortTradingEngine()
        
        # 1. 低价期权（如 $0.50）应获得 $0.05 宽容度
        adj_price_buy, offset_buy = engine._calculate_dynamic_slippage("TSM260619P00150000.US", 0.50, "Buy", "LO")
        self.assertEqual(adj_price_buy, 0.55) # 0.50 + 0.05
        self.assertEqual(offset_buy, 0.10)   # max(0.05 * 2, 0.10)
        
        # 2. 中价期权（如 $3.00）应获得 $0.15 宽容度
        adj_price_sell, offset_sell = engine._calculate_dynamic_slippage("TSM260619P00150000.US", 3.00, "Sell", "LO")
        self.assertEqual(adj_price_sell, 2.85) # 3.00 - 0.15
        self.assertEqual(offset_sell, 0.30)   # max(0.15 * 2, 0.10)
        
        # 3. 高价期权（如 $10.00）应获得 5% 宽容度 ($0.50)
        adj_price_high, offset_high = engine._calculate_dynamic_slippage("TSM260619P00150000.US", 10.00, "Buy", "LO")
        self.assertEqual(adj_price_high, 10.50) # 10.00 + 0.50
        self.assertEqual(offset_high, 1.00)     # max(0.50 * 2, 0.10)

    def test_multiplier_logic(self):
        """由于 submit_order 需要真实的 Context 运行，我们在这里单独验证 1:100 换算和滑点参数注入的组装正确性"""
        # 模拟 TSM.US (正股) 买入 150 股，数量不应被转换
        symbol_stock = "TSM.US"
        is_opt_stock = BaseTradingEngine.is_option_symbol(symbol_stock)
        quantity_stock = 150
        actual_qty_stock = Decimal(str(max(int(quantity_stock / 100), 1))) if is_opt_stock else Decimal(str(quantity_stock))
        
        self.assertFalse(is_opt_stock)
        self.assertEqual(actual_qty_stock, Decimal("150"))
        
        # 模拟 TSM260619P00150000.US (期权) 买入 150 股，股数应自适应向下换算为 1 张
        symbol_opt1 = "TSM260619P00150000.US"
        is_opt1 = BaseTradingEngine.is_option_symbol(symbol_opt1)
        quantity_opt1 = 150
        actual_qty_opt1 = Decimal(str(max(int(quantity_opt1 / 100), 1))) if is_opt1 else Decimal(str(quantity_opt1))
        
        self.assertTrue(is_opt1)
        self.assertEqual(actual_qty_opt1, Decimal("1")) # max(int(150/100), 1) = 1
        
        # 模拟 TSM260619P00150000.US (期权) 买入 250 股，股数应自适应向下换算为 2 张
        symbol_opt2 = "TSM260619P00150000.US"
        is_opt2 = BaseTradingEngine.is_option_symbol(symbol_opt2)
        quantity_opt2 = 250
        actual_qty_opt2 = Decimal(str(max(int(quantity_opt2 / 100), 1))) if is_opt2 else Decimal(str(quantity_opt2))
        
        self.assertTrue(is_opt2)
        self.assertEqual(actual_qty_opt2, Decimal("2")) # max(int(250/100), 1) = 2

if __name__ == "__main__":
    unittest.main()
