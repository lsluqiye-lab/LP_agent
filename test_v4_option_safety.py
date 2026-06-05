# test_v4_option_safety.py
"""
LP-Agent V4.0 期权物理风控安全网与 DRY_RUN 沙盒模拟阻断专项测试
"""
import unittest
import logging
import os
from unittest.mock import MagicMock, patch
from tools.engines.longport_engine import LongPortTradingEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

class TestOptionSafetyInterceptors(unittest.TestCase):

    def setUp(self):
        # 激活 mock 掉底层的 TradeContext 链接
        self.patcher = patch('tools.engines.longport_engine.TradeContext')
        self.mock_trade_context_class = self.patcher.start()
        self.mock_trade_context = MagicMock()
        self.mock_trade_context_class.return_value = self.mock_trade_context
        
        # 实例化引擎
        self.engine = LongPortTradingEngine()

    def tearDown(self):
        self.patcher.stop()
        if "TRADING_DRY_RUN" in os.environ:
            del os.environ["TRADING_DRY_RUN"]

    def test_max_contracts_interceptor(self):
        """测试单笔期权最大张数（5张）物理红线硬拦截"""
        # 模拟买入 600 股（折算为 6 张合约），应该被拒绝并抛出异常
        with self.assertRaises(ValueError) as context:
            self.engine.submit_order(
                symbol="TSM260619P00150000.US",
                side="Buy",
                order_type="MO",
                quantity=600  # 600股 -> 6张合约
            )
        
        self.assertIn("超过安全物理红线", str(context.exception))
        logging.info("✅ 成功验证：单笔下单期权合约超限硬拦截通过！")

    def test_uncovered_sell_call_interceptor(self):
        """测试裸 Sell Call 风控拦截（无足额正股底仓）"""
        # Mock 持仓：当前无任何持仓
        self.engine.get_positions = MagicMock(return_value=[])
        
        # 卖出 TSM.US 的 150 股 Call（折算为 1 张合约，需要 100 股 TSM 正股）
        # 应该被拒绝并抛出 ValueError
        with self.assertRaises(ValueError) as context:
            self.engine.submit_order(
                symbol="TSM260619C00150000.US",
                side="Sell",
                order_type="LO",
                quantity=150,  # 折算为 1 张 Call
                price=2.50
            )
        
        self.assertIn("强行拦截裸 Sell Call", str(context.exception))
        logging.info("✅ 成功验证：裸 Sell Call 硬拦截风控通过！")

        # 模拟持有 50 股正股（依然不够 100 股）
        self.engine.get_positions = MagicMock(return_value=[
            {"symbol": "TSM.US", "quantity": 50.0, "cost_price": 140.0, "market_value": 7000.0}
        ])
        with self.assertRaises(ValueError) as context_insufficient:
            self.engine.submit_order(
                symbol="TSM260619C00150000.US",
                side="Sell",
                order_type="LO",
                quantity=150,  # 需要 100 股
                price=2.50
            )
        self.assertIn("强行拦截裸 Sell Call", str(context_insufficient.exception))
        logging.info("✅ 成功验证：正股持仓不足开备兑 Call 硬拦截风控通过！")

        # 模拟持有 100 股正股（足额），此时应当不触发拦截
        self.engine.get_positions = MagicMock(return_value=[
            {"symbol": "TSM.US", "quantity": 100.0, "cost_price": 140.0, "market_value": 14000.0}
        ])
        # 开启 DRY_RUN 避免真实调用提交
        os.environ["TRADING_DRY_RUN"] = "true"
        resp = self.engine.submit_order(
            symbol="TSM260619C00150000.US",
            side="Sell",
            order_type="LO",
            quantity=100,  # 1 张
            price=2.50
        )
        self.assertTrue(resp["success"])
        self.assertIn("DRY-RUN-MOCK", resp["order_id"])
        logging.info("✅ 成功验证：持有足额正股时，备兑 Call 安全放行通过！")

    def test_uncovered_sell_put_interceptor(self):
        """测试裸 Sell Put 现金保障不足风控拦截"""
        # 行权价 $150，卖出 100 股（折算为 1 张 Put），需要 $15,000 现金担保
        # 1. 模拟现金账户只有 $5,000，应该被拦截
        self.engine.get_account_balance = MagicMock(return_value={
            "buying_power": 5000.0, "cash": 5000.0, "net_assets": 5000.0
        })
        
        with self.assertRaises(ValueError) as context:
            self.engine.submit_order(
                symbol="TSM260619P00150000.US",
                side="Sell",
                order_type="LO",
                quantity=100,  # 1张
                price=2.50
            )
        self.assertIn("现金备兑 Put 资金保障不足", str(context.exception))
        logging.info("✅ 成功验证：现金担保不足卖出 Put 硬拦截风控通过！")

        # 2. 模拟现金账户有 $20,000，此时应该足额放行
        self.engine.get_account_balance = MagicMock(return_value={
            "buying_power": 20000.0, "cash": 20000.0, "net_assets": 20000.0
        })
        os.environ["TRADING_DRY_RUN"] = "true"
        resp = self.engine.submit_order(
            symbol="TSM260619P00150000.US",
            side="Sell",
            order_type="LO",
            quantity=100,  # 1张
            price=2.50
        )
        self.assertTrue(resp["success"])
        self.assertIn("DRY-RUN-MOCK", resp["order_id"])
        logging.info("✅ 成功验证：担保现金充足时，现金备兑 Put 放行通过！")

    def test_trading_dry_run_sandbox(self):
        """测试 TRADING_DRY_RUN 沙盒模拟下单，绝对零实盘资金损耗"""
        os.environ["TRADING_DRY_RUN"] = "true"
        
        # 模拟购买 10 股 TSM 正股
        resp = self.engine.submit_order(
            symbol="TSM",
            side="Buy",
            order_type="LO",
            quantity=10,
            price=140.0
        )
        
        self.assertTrue(resp["success"])
        self.assertIn("DRY-RUN-MOCK-", resp["order_id"])
        self.assertEqual(resp["raw_response"], "DRY_RUN_MOCK_RESP")
        # 校验底层的真正长桥 submit_order 绝未被调用过
        self.mock_trade_context.submit_order.assert_not_called()
        logging.info("✅ 成功验证：TRADING_DRY_RUN 开启时，跳过真实调用，下单模拟阻断成功！")

    def test_protective_put_pairing_interceptor(self):
        """测试保护性看跌期权（Protective Put）正股少于 100 股超额对冲硬拦截"""
        # 1. 模拟当前持仓仅有 30 股 VRT（不足对冲 1 张合约所需的 100 股）
        self.engine.get_positions = MagicMock(return_value=[
            {"symbol": "VRT.US", "quantity": 30.0, "cost_price": 310.0, "market_value": 9300.0}
        ])
        
        # 购买 30 股对应的 VRT Put（系统换算为 1 张 Put，需要 100 股正股）
        # 应该被拒绝并抛出 ValueError
        with self.assertRaises(ValueError) as context:
            self.engine.submit_order(
                symbol="VRT260618P295000.US",
                side="Buy",
                order_type="MO",
                quantity=30  # 向上取整换算为 1 张 Put
            )
        
        self.assertIn("强行拦截保护性看跌期权", str(context.exception))
        logging.info("✅ 成功验证：迷你持仓下购买 Protective Put 超额对冲拦截成功！")

        # 2. 模拟当前持仓达到 100 股 VRT（足额配对）
        self.engine.get_positions = MagicMock(return_value=[
            {"symbol": "VRT.US", "quantity": 100.0, "cost_price": 310.0, "market_value": 31000.0}
        ])
        os.environ["TRADING_DRY_RUN"] = "true"
        resp = self.engine.submit_order(
            symbol="VRT260618P295000.US",
            side="Buy",
            order_type="MO",
            quantity=100  # 1 张 Put
        )
        self.assertTrue(resp["success"])
        self.assertIn("DRY-RUN-MOCK", resp["order_id"])
        logging.info("✅ 成功验证：持有足额正股（100股）时，1张 Protective Put 配对对冲安全通过！")

if __name__ == "__main__":
    unittest.main()
