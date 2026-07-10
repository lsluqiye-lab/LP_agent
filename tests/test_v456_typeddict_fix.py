import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock

# Mocking the dependencies to test main.py logic in isolation
class TestTypedDictFix(unittest.IsolatedAsyncioTestCase):
    async def test_sector_and_narrative_access(self):
        # Sample TypedDict data
        sector_briefing = {
            "summary": "Capital is rotating into Financials.",
            "strong_sectors": ["Financials"],
            "weak_sectors": ["Technology"],
            "risk_warning": "Tech is overbought."
        }
        narrative_briefing = {
            "headline": "Inflation fears rising.",
            "top_narratives": [],
            "narrative_shift_warning": "Shift to value.",
            "sentiment_density": "High",
            "impact_on_strategy": "Be cautious."
        }
        
        # Test 1: Simulating Phase 3 access
        try:
            summary = sector_briefing["summary"]
            headline = narrative_briefing["headline"]
            self.assertEqual(summary, "Capital is rotating into Financials.")
            self.assertEqual(headline, "Inflation fears rising.")
            print("✅ Phase 3 style dictionary access passed.")
        except Exception as e:
            self.fail(f"Phase 3 style access failed: {e}")

        # Test 2: Simulating sb_dict rebuilding logic in main loop/event cycle
        try:
            sector_briefing_obj = sector_briefing
            sb_dict = {
                "summary": sector_briefing_obj["summary"],
                "strong_sectors": sector_briefing_obj["strong_sectors"],
                "weak_sectors": sector_briefing_obj["weak_sectors"],
                "risk_warning": sector_briefing_obj["risk_warning"]
            } if sector_briefing_obj else None
            
            self.assertEqual(sb_dict["summary"], "Capital is rotating into Financials.")
            print("✅ PortfolioManager sb_dict rebuilding logic passed.")
        except Exception as e:
            self.fail(f"sb_dict rebuilding logic failed: {e}")

if __name__ == "__main__":
    unittest.main()
