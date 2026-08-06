import unittest
from unittest.mock import MagicMock
import json
import logging

from agent.portfolio_manager import PortfolioManager

class TestWeedProtection(unittest.TestCase):
    def setUp(self):
        # 实例化组合管理器
        self.pm = PortfolioManager()
        # Mock 掉 trade_logger.get_recent_logs 方法
        self.pm.trade_logger.get_recent_logs = MagicMock()

    def test_recently_weeded_out_extraction(self):
        # 1. 模拟过去 3 天内发生的交易日志
        from datetime import datetime, timedelta
        today_str = datetime.now().strftime("%Y-%m-%d")
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        mock_logs = [
            {
                "date": today_str,
                "trades": [
                    {
                        "symbol": "GOOGL",
                        "side": "Sell",
                        "order_type": "MO",
                        "reason": "GOOGL inside weed_out_list, active liquidation to free up cash for stronger leaders.",
                        "quantity": 24
                    },
                    {
                        "symbol": "AMZN",
                        "side": "Sell",
                        "order_type": "MO",
                        "reason": "根据投资组合指令 weed_out_list 强制淘汰弱势持仓 AMZN",
                        "quantity": 35
                    },
                    {
                        "symbol": "NVDA",
                        "side": "Buy",
                        "reason": "突破加仓",
                        "quantity": 10
                    }
                ]
            },
            {
                "date": yesterday_str,
                "trades": [
                    {
                        "symbol": "UNH",
                        "side": "Sell",
                        "order_type": "MO",
                        "reason": "根据投资组合指令(Portfolio Directives)，UNH已被列入weed_out_list(淘汰名单)，属于低效占用资金的杂草",
                        "quantity": 24
                    }
                ]
            }
        ]
        
        self.pm.trade_logger.get_recent_logs.return_value = mock_logs

        # 2. 调用分析，即便当前持仓为空
        directives = self.pm.analyze_portfolio(
            current_positions=[],
            account_balance={},
            macro_risk={"score": 75.0, "constraints": {"allow_new_buy": True}}
        )

        print("\nGenerated directives:")
        print(json.dumps(directives, indent=2, ensure_ascii=False))

        # 3. 断言验证
        # 应该包含最近3天作为杂草淘汰卖出的 GOOGL, AMZN, UNH
        recently_weeded = [r["symbol"] for r in directives.get("recently_weeded_out", [])]
        self.assertIn("GOOGL", recently_weeded)
        self.assertIn("AMZN", recently_weeded)
        self.assertIn("UNH", recently_weeded)
        
        # 绝对不应该包含买入的 NVDA
        self.assertNotIn("NVDA", recently_weeded)
        
        # 即使空仓，也应计算并正确返回该保护禁买名单
        self.assertEqual(len(recently_weeded), 3)
        print("✅ Unit Test passed successfully!")

    def test_stop_loss_cooldown_extraction(self):
        # 1. 模拟过去 3 天内发生过硬性止损/亏损割肉和保护性止盈的交易日志
        from datetime import datetime
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        mock_logs = [
            {
                "date": today_str,
                "trades": [
                    {
                        "symbol": "VRT",
                        "side": "Sell",
                        "order_type": "MO",
                        "reason": "VRT跌破SMA50(312.3)生命线，趋势转为Stage 3，亏损达9.72%，执行硬性止损清仓。",
                        "quantity": 30
                    },
                    {
                        "symbol": "TSM",
                        "side": "Sell",
                        "order_type": "MO",
                        "reason": "TSM 盈利 7.24%，挂设自适应追踪止损锁定利润并高位止盈",
                        "quantity": 30
                    }
                ]
            }
        ]
        self.pm.trade_logger.get_recent_logs.return_value = mock_logs

        # 2. 调用分析
        directives = self.pm.analyze_portfolio(
            current_positions=[],
            account_balance={},
            macro_risk={"score": 75.0, "constraints": {"allow_new_buy": True}}
        )

        recently_weeded = [r["symbol"] for r in directives.get("recently_weeded_out", [])]
        
        # 3. 断言验证：硬性止损的 VRT 必须进入保护名单
        self.assertIn("VRT", recently_weeded)
        
        # 正常止盈锁利的 TSM 不应该进入该保护名单，依然允许买回
        self.assertNotIn("TSM", recently_weeded)
        
        self.assertEqual(len(recently_weeded), 1)
        print("✅ Unit Test (Stop Loss Cooldown) passed successfully!")

if __name__ == "__main__":
    unittest.main()
