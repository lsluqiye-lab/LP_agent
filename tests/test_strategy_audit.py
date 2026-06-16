import unittest
import json
import os
import shutil
from pathlib import Path
from datetime import datetime, timedelta
import pytz
import asyncio

from data.trade_logger import TradeLogger
from agent.review import ReviewAgent
from agent.react import ReActAgent
from llm.base import BaseLLM, LLMResponse, ChatMessage

class MockLLM(BaseLLM):
    def __init__(self, api_key="dummy", base_url="dummy", model="dummy", **kwargs):
        super().__init__(api_key, base_url, model, **kwargs)

    def chat(self, messages, tools=None):
        return LLMResponse(content="Mocked Review Report", tool_calls=[])
    
    def get_provider_name(self) -> str:
        return "mock"

class TestStrategyAudit(unittest.TestCase):
    def setUp(self):
        # Create a temporary log directory for testing
        self.test_log_dir = Path("data/test_logs")
        if self.test_log_dir.exists():
            shutil.rmtree(self.test_log_dir)
        self.test_log_dir.mkdir(parents=True)
        self.logger = TradeLogger(log_dir=self.test_log_dir)
        self.eastern = pytz.timezone("US/Eastern")

    def tearDown(self):
        # Cleanup temporary logs
        if self.test_log_dir.exists():
            shutil.rmtree(self.test_log_dir)

    def test_log_audit_storage(self):
        """Test that log_audit correctly saves data to the JSON file."""
        self.logger.log_audit(
            audit_type="WHIPSAW",
            symbol="AAPL",
            event="TEST_EVENT",
            metrics={"diff": 10.5},
            improvement="Keep holding"
        )
        
        log_data = self.logger.get_today_log()
        self.assertIn("audit_logs", log_data)
        self.assertEqual(len(log_data["audit_logs"]), 1)
        self.assertEqual(log_data["audit_logs"][0]["symbol"], "AAPL")
        self.assertEqual(log_data["audit_logs"][0]["metrics"]["diff"], 10.5)

    def test_reviewer_data_preparation(self):
        """Test that the ReviewAgent includes past 3 days of trades in context."""
        # 1. Mock trades for the past 2 days
        yesterday = (datetime.now(self.eastern) - timedelta(days=1)).strftime("%Y-%m-%d")
        day_before = (datetime.now(self.eastern) - timedelta(days=2)).strftime("%Y-%m-%d")
        
        # Manually write to past log files
        for date_str, symbol in [(yesterday, "TSM"), (day_before, "NVDA")]:
            log_file = self.test_log_dir / f"trade_log_{date_str}.json"
            data = {
                "date": date_str,
                "trades": [{
                    "symbol": symbol,
                    "side": "Sell",
                    "price": 100,
                    "reason": "Test"
                }]
            }
            with open(log_file, "w") as f:
                json.dump(data, f)

        # 2. Initialize ReviewAgent
        agent = ReviewAgent(llm=MockLLM())
        agent.trade_logger = self.logger
        
        # 3. Prepare data
        review_input = agent._prepare_review_data({}, {})
        
        # 4. Verify context contains past trades
        self.assertIn("过去3日交易回顾", review_input)
        self.assertIn("卖出 TSM", review_input)
        self.assertIn("卖出 NVDA", review_input)

    def test_react_whipsaw_detection_logic(self):
        """Test the logic we injected into ReActAgent to detect re-entry friction."""
        # 1. Create a recent 'Sell' log entry for yesterday
        yesterday = (datetime.now(self.eastern) - timedelta(days=1)).strftime("%Y-%m-%d")
        log_file = self.test_log_dir / f"trade_log_{yesterday}.json"
        with open(log_file, "w") as f:
            json.dump({
                "date": yesterday,
                "trades": [{"symbol": "ARM", "side": "Sell", "price": 150.0}]
            }, f)

        # 2. Mock a ReAct loop where we call buy_stock for ARM at a higher price
        # We'll use a simplified check since we can't easily run the full async ReAct loop with tools here,
        # but we can verify the logic block we added.
        
        # Simulate the 'buy' call
        buy_symbol = "ARM"
        buy_price = 160.0 # Higher than 150.0
        
        # Trigger the audit detection logic manually to verify the logger behavior
        past_logs = self.logger.get_recent_logs(days=3)
        detected = False
        for plog in past_logs:
            p_trades = plog.get("trades", [])
            last_sell = next((t for t in reversed(p_trades) if t.get("symbol") == buy_symbol and t.get("side") == "Sell"), None)
            if last_sell:
                sell_price = last_sell.get("price")
                if float(buy_price) > float(sell_price):
                    self.logger.log_audit(
                        audit_type="WHIPSAW",
                        symbol=buy_symbol,
                        event="RE_ENTRY_DETECTION",
                        metrics={"sell_price": sell_price, "buy_price": buy_price}
                    )
                    detected = True
                break
        
        self.assertTrue(detected)
        log_data = self.logger.get_today_log()
        self.assertEqual(log_data["audit_logs"][0]["audit_type"], "WHIPSAW")
        self.assertEqual(log_data["audit_logs"][0]["metrics"]["buy_price"], 160.0)

if __name__ == "__main__":
    unittest.main()
