
import unittest
from unittest.mock import MagicMock, patch
from decimal import Decimal
import json
import logging

# Set up logging to avoid clutter
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_v4_unit_fix")

# Import the components under test
from tools.engines.longport_engine import LongPortTradingEngine
from tools.trading import auto_align_trailing_stops

class TestV4UnitFix(unittest.TestCase):

    def setUp(self):
        # Mock the config and context
        self.mock_config = MagicMock()
        with patch('tools.engines.longport_engine.TradeContext') as mock_ctx_cls:
            self.mock_ctx = MagicMock()
            mock_ctx_cls.return_value = self.mock_ctx
            self.engine = LongPortTradingEngine()
            self.engine._config = self.mock_config
            self.engine._trade_ctx = self.mock_ctx

    def test_option_symbol_detection(self):
        """测试期权代码判定"""
        self.assertTrue(self.engine.is_option_symbol("AAPL260619C00200000.US"))
        self.assertTrue(self.engine.is_option_symbol("QQQ260626P676000"))
        self.assertFalse(self.engine.is_option_symbol("AAPL.US"))
        self.assertFalse(self.engine.is_option_symbol("NVDA"))

    def test_submit_order_stock_quantity(self):
        """测试正股下单数量（不转换）"""
        self.engine.submit_order(symbol="NVDA", side="Buy", order_type="MO", quantity=10)
        self.mock_ctx.submit_order.assert_called()
        args, kwargs = self.mock_ctx.submit_order.call_args
        self.assertEqual(kwargs['submitted_quantity'], Decimal('10'))

    def test_submit_order_option_quantity_conversion(self):
        """测试期权下单数量转换 (股转张)"""
        # 100 股 -> 1 张
        self.engine.submit_order(symbol="QQQ260626P676000.US", side="Buy", order_type="MO", quantity=100)
        args, kwargs = self.mock_ctx.submit_order.call_args
        self.assertEqual(kwargs['submitted_quantity'], Decimal('1'))

        # 250 股 -> 3 张 (round 2.5 -> 3, using round() which is round-to-even in Python, but 2.5 usually rounds to 2, let's check)
        # Actually int(round(150/100)) = 2. 
        self.engine.submit_order(symbol="QQQ260626P676000.US", side="Buy", order_type="MO", quantity=251)
        args, kwargs = self.mock_ctx.submit_order.call_args
        self.assertEqual(kwargs['submitted_quantity'], Decimal('3'))

        # 1 股 -> 1 张 (min 1)
        self.engine.submit_order(symbol="QQQ260626P676000.US", side="Buy", order_type="MO", quantity=1)
        args, kwargs = self.mock_ctx.submit_order.call_args
        self.assertEqual(kwargs['submitted_quantity'], Decimal('1'))

    def test_get_positions_unit_conversion(self):
        """测试持仓单位转换 (张转股)"""
        # Mock longport response
        mock_pos = MagicMock()
        mock_pos.symbol = "QQQ260626P676000.US"
        mock_pos.available_quantity = Decimal('2')
        mock_pos.cost_price = Decimal('1000') # $10.00 if per contract, but engine returns it as is
        
        mock_channel = MagicMock()
        mock_channel.positions = [mock_pos]
        
        self.mock_ctx.stock_positions.return_value.channels = [mock_channel]
        
        positions = self.engine.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['quantity'], 200.0) # 2 contracts * 100
        self.assertEqual(positions[0]['symbol'], "QQQ260626P676000.US")

    def test_get_today_orders_unit_conversion(self):
        """测试今日订单单位转换 (张转股)"""
        mock_order = MagicMock()
        mock_order.symbol = "QQQ260626P676000.US"
        mock_order.quantity = Decimal('5')
        mock_order.executed_quantity = Decimal('2')
        mock_order.status = "Filled"
        mock_order.side = "Buy"
        mock_order.order_type = "LO"
        
        self.mock_ctx.today_orders.return_value = [mock_order]
        
        orders = self.engine.get_today_orders()
        self.assertEqual(orders[0]['quantity'], 500.0)
        self.assertEqual(orders[0]['executed_quantity'], 200.0)

    @patch('tools.engines.get_trading_engine')
    def test_auto_align_logic(self, mock_get_engine):
        """测试自动对齐逻辑 (使用已转换的股数)"""
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine
        
        # 持仓 200 股 QQQ Put
        mock_engine.get_positions.return_value = [
            {"symbol": "QQQ260626P676000.US", "quantity": 200.0}
        ]
        
        # 现有挂单也是 200 股
        mock_engine.get_today_orders.return_value = [
            {
                "symbol": "QQQ260626P676000.US",
                "quantity": 200.0,
                "side": "OrderSide.Sell", 
                "order_type": "OrderType.TSLPPCT", 
                "order_id": "order_123",
                "llm_status": "OrderStatus.New (NEW)", 
                "raw_order": MagicMock(trailing_percent=5.0)
            }
        ]
        
        result = auto_align_trailing_stops()
        # 应该不触发重新对齐
        self.assertEqual(result, "Successfully realigned 0 stops.")
        mock_engine.cancel_order.assert_not_called()

        # 如果数量不一致: 持仓 300 股，挂单 200 股
        mock_engine.get_positions.return_value = [
            {"symbol": "QQQ260626P676000.US", "quantity": 300.0}
        ]
        result = auto_align_trailing_stops()
        self.assertEqual(result, "Successfully realigned 1 stops.")
        mock_engine.cancel_order.assert_called_with("order_123")
        mock_engine.submit_order.assert_called()


if __name__ == '__main__':
    unittest.main()
