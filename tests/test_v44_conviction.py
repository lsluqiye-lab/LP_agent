
import unittest
import json
import logging
from unittest.mock import MagicMock, patch
from tools.trading import BuyStockTool, SellStockTool
from agent.portfolio_manager import PortfolioManager

logging.basicConfig(level=logging.INFO)

class TestV44Conviction(unittest.TestCase):

    def setUp(self):
        self.buy_tool = BuyStockTool()
        self.sell_tool = SellStockTool()
        self.pm = PortfolioManager()

    @patch('tools.trading.get_trading_engine')
    @patch('tools.trading.get_trade_logger')
    @patch('tools.market_data.get_quote_ctx')
    @patch('config.WATCHLIST', ['ASML', 'GE'])
    def test_buy_tool_v_recovery(self, mock_quote_ctx, mock_trade_logger, mock_engine):
        """测试 V-Recovery 纠偏回补逻辑是否能穿透冷却期"""
        symbol = "ASML"
        
        # 模拟引擎返回
        mock_engine_inst = mock_engine.return_value
        mock_engine_inst.get_account_balance.return_value = {"net_assets": 100000, "cash": 50000}
        mock_engine_inst.get_positions.return_value = []
        
        # 模拟 TradeLogger 返回风险评分和最近止损记录
        mock_logger_inst = mock_trade_logger.return_value
        mock_logger_inst.get_latest_risk_score.return_value = {
            "score": 85.0,
            "components": {"sentiment": 90, "rsi_breadth": 100}
        }
        
        # 模拟最近 3 天有 ASML 的 HARD_STOP 记录
        with patch('agent.portfolio_manager.PortfolioManager.analyze_portfolio') as mock_analyze:
            mock_analyze.return_value = {
                "recently_weeded_out": [{"symbol": "ASML", "type": "HARD_STOP", "days_left": 2}]
            }
            
            # 1. 正常买入：应该被拦截
            res_normal = self.buy_tool.execute(symbol=symbol, quantity=10, order_type="LIT", trigger_price=1750, price=1755, conviction="normal")
            self.assertIn("Whipsaw 拦截", res_normal)
            logging.info("✅ 成功拦截常规回补 (Whipsaw Guard Working)")

            # 2. 强行回补 (force_recovery=True, conviction='high')：应该通过拦截
            # 需要模拟提交订单成功
            mock_engine_inst.submit_order.return_value = {"order_id": "test_id_v_recovery"}
            
            # 模拟报价获取成功 (用于仓位缩减计算)
            mock_q_ctx_inst = mock_quote_ctx.return_value
            mock_q_res = MagicMock()
            mock_q_res.last_done = 1750
            mock_q_ctx_inst.quote.return_value = [mock_q_res]

            res_recovery = self.buy_tool.execute(symbol=symbol, quantity=10, order_type="LIT", trigger_price=1750, price=1755, conviction="high", force_recovery=True)
            self.assertIn("V-Recovery 纠偏回补", res_recovery)
            self.assertIn("test_id_v_recovery", res_recovery)
            logging.info("✅ 成功执行 V-Recovery 强行回补 (Exception Logic Working)")

    @patch('tools.trading.get_trading_engine')
    @patch('tools.trading.get_trade_logger')
    @patch('tools.market_data.get_quote_ctx')
    def test_sell_tool_high_conviction_prr(self, mock_quote_ctx, mock_trade_logger, mock_engine):
        """测试 High Conviction 下 PRR 锁利算法的宽容度"""
        symbol = "GE"
        
        # 模拟持仓：成本 300，现价 330 (浮盈 10%)
        mock_engine_inst = mock_engine.return_value
        mock_engine_inst.get_positions.return_value = [
            {"symbol": "GE.US", "quantity": 100, "cost_price": 300, "market_value": 33000}
        ]
        mock_engine_inst.get_today_orders.return_value = []
        mock_engine_inst.submit_order.return_value = {"order_id": "sell_id_1"}

        # 模拟报价
        mock_q_ctx_inst = mock_quote_ctx.return_value
        mock_q_res = MagicMock()
        mock_q_res.last_done = 330
        mock_q_ctx_inst.quote.return_value = [mock_q_res]

        # 1. 正常信心：10% 浮盈触发 PRR 收网 (3% 门槛)
        # t_target = (0.5 * 30 / 330) * 100 = 4.54%
        
        # 确保日志级别
        logging.getLogger('tools.trading').setLevel(logging.INFO)
        with self.assertLogs(level='INFO') as cm:
            self.sell_tool.execute(symbol=symbol, quantity=100, order_type="TSMPCT", trailing_percent=10.0, conviction="normal")
            self.assertTrue(any("PRR Guard" in output and "收网收紧" in output for output in cm.output))
        logging.info("✅ 正常信心下 PRR 成功收网")

        # 2. 高信心：8% 门槛。5% 浮盈不应触发收网。
        # 模拟利润 5% (300 -> 315)
        mock_q_res.last_done = 315
        # t_target = (0.3 * 15 / 315) * 100 = 1.42% (小于 8% 门槛)
        
        with self.assertLogs(level='INFO') as cm_high:
            logging.info("DUMMY LOG TO PREVENT ASSERTION ERROR")
            self.sell_tool.execute(symbol=symbol, quantity=100, order_type="TSMPCT", trailing_percent=10.0, conviction="high")
            # 检查是否没有 PRR 收紧日志（排除 DUMMY LOG）
            self.assertFalse(any("收网收紧" in output for output in cm_high.output if "DUMMY" not in output))
        logging.info("✅ 高信心下 PRR 保持宽容 (High Threshold Working)")

if __name__ == '__main__':
    unittest.main()
