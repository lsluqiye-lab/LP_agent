import unittest
import asyncio
import json
import os
from datetime import datetime
import pytz
from unittest.mock import MagicMock, patch
from agent.alpha_scanner import AlphaScanner
from agent.orchestrator import ExpertOrchestrator
from agent.react import STRATEGIC_SYSTEM_PROMPT

class TestV44QuantBridge(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_llm = MagicMock()
        # Mocking the watchlist path to a temp file
        self.watchlist_path = "data/daily_watchlist.json"

    def test_alpha_scanner_metadata_structure(self):
        """验证选股神器输出是否包含详细元数据"""
        scanner = AlphaScanner(llm=self.mock_llm)
        
        # Mock LLM response for AlphaScanner
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "sectors": ["Tech"],
            "watchlist_detail": {
                "NVDA": {"score": 95, "rank": 1, "conviction": "Tier 1", "reason": "AI Leader"},
                "AAPL": {"score": 80, "rank": 5, "conviction": "Tier 2", "reason": "Strong Cash"}
            },
            "reasoning": "Test reasoning"
        })
        self.mock_llm.chat.return_value = mock_response

        # Mock holdings and search
        with patch.object(scanner, '_get_current_holdings', return_value=[]), \
             patch.object(scanner, 'quant_analyst') as mock_quant:
            mock_quant.get_sector_strength.return_value = {"Technology": 100}
            mock_quant.get_alpha_scores.return_value = {"NVDA": {"score": 95}, "AAPL": {"score": 80}}
            
            scanner.run()
            
        # Check if file exists and has correct structure
        self.assertTrue(os.path.exists(self.watchlist_path))
        with open(self.watchlist_path, "r") as f:
            data = json.load(f)
            self.assertIn("watchlist_detail", data)
            self.assertEqual(data["watchlist_detail"]["NVDA"]["score"], 95)
            self.assertEqual(data["watchlist_detail"]["NVDA"]["conviction"], "Tier 1")

    async def test_orchestrator_metadata_loading(self):
        """验证 Orchestrator 是否能正确加载并传递量化元数据"""
        orchestrator = ExpertOrchestrator(llm=self.mock_llm)
        
        # Ensure a test watchlist exists
        test_data = {
            "date": datetime.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d"),
            "watchlist_detail": {
                "NVDA": {"score": 98, "rank": 1, "conviction": "Tier 1", "reason": "Quant Alpha"}
            }
        }
        os.makedirs("data", exist_ok=True)
        with open(self.watchlist_path, "w") as f:
            json.dump(test_data, f)

        # Mock experts as async
        orchestrator.f_analyst.analyze = MagicMock(return_value=asyncio.Future())
        orchestrator.f_analyst.analyze.return_value.set_result({"valuation": "Fair Value", "summary": "..."})
        
        orchestrator.t_analyst.analyze = MagicMock(return_value=asyncio.Future())
        orchestrator.t_analyst.analyze.return_value.set_result({"trend_stage": "Stage 2", "summary": "...", "is_volume_breakout": True})
        
        orchestrator.s_analyst.analyze = MagicMock(return_value=asyncio.Future())
        orchestrator.s_analyst.analyze.return_value.set_result({"market_sentiment": "Greed", "summary": "..."})
        
        # Use patch for gather but let it behave normally or return a future
        future_results = asyncio.Future()
        future_results.set_result([{"valuation": "Fair Value"}, {"trend_stage": "Stage 2"}, {"market_sentiment": "Greed"}])
        
        with patch('asyncio.gather', return_value=future_results):
            briefing = await orchestrator.get_full_briefing("NVDA", MagicMock(), MagicMock())
            self.assertIn("quant_metadata", briefing)
            self.assertEqual(briefing["quant_metadata"]["score"], 98)
            self.assertEqual(briefing["quant_metadata"]["conviction"], "Tier 1")

    def test_cio_prompt_v44_inclusion(self):
        """验证 CIO Prompt 是否包含 V4.4 的量化指令"""
        self.assertIn("quant_metadata", STRATEGIC_SYSTEM_PROMPT)
        self.assertIn("Alpha Score Sizing", STRATEGIC_SYSTEM_PROMPT)
        self.assertIn("Rank 1-3", STRATEGIC_SYSTEM_PROMPT)

if __name__ == "__main__":
    unittest.main()
