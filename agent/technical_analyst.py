"""
TechnicalAnalyst Agent
"""
import json
from agent.schemas import TechnicalBriefing, TrendStage
from llm.base import BaseLLM, ChatMessage, Role
from tools.market_data import GetTechnicalAnalysisTool

class TechnicalAnalyst:
    """
    The TechnicalAnalyst agent analyzes price action, volume, and technical indicators.
    It uses GetTechnicalAnalysisTool for data and BaseLLM for interpretation.
    """

    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.tech_tool = GetTechnicalAnalysisTool()

    async def analyze(self, symbol: str) -> TechnicalBriefing:
        print(f"[{self.__class__.__name__}] Performing technical analysis for {symbol}...")

        # Step 1: Get raw technical data
        # Note: BaseTool.execute is synchronous in this project
        raw_data_json = self.tech_tool.execute(symbol=symbol)
        raw_data = json.loads(raw_data_json)

        if "error" in raw_data:
            raise ValueError(f"Technical data error: {raw_data['error']}")

        # Step 2: Interpret with LLM
        prompt = self._build_interpretation_prompt(symbol, raw_data)
        
        messages = [
            ChatMessage(role=Role.SYSTEM, content="You are a professional CMT (Chartered Market Technician)."),
            ChatMessage(role=Role.USER, content=prompt)
        ]
        
        print(f"[{self.__class__.__name__}] Interpreting charts and indicators with {self.llm.model}...")
        response = self.llm.chat(messages)
        
        if not response.content:
            raise ValueError("LLM returned empty interpretation.")

        # Step 3: Parse and return
        return self._parse_llm_response(response.content)

    def _build_interpretation_prompt(self, symbol: str, data: dict) -> str:
        trend_options = ", ".join(f'"{s}"' for s in TrendStage.__args__)
        
        return f"""
Analyze the technical data for {symbol} and provide a structured briefing.
Focus on identifying the Mark Minervini 'Stage 2' characteristics, key support/resistance, momentum signals, and critically, the Price-Volume (量价) relationship.

TECHNICAL DATA:
{json.dumps(data, indent=2)}

---
REQUIREMENTS:
1. Determine Trend Stage: {trend_options} (Stage 2 is the preferred buying zone).
2. Analyze Volume-Price Relationship: Check if volume supports the price trend (e.g., high volume on breakouts/green days, low volume on pullbacks/red days), analyze OBV divergence, and volume ratios.
3. Identify key signals (e.g., Golden Cross, RSI Divergence, Volume Spikes).
4. Extract Support and Resistance levels from the price history.
5. Provide a concise summary of the technical setup.

Format as a single JSON object:
{{
  "trend_stage": "...",
  "summary": "...",
  "volume_price_analysis": "...",
  "key_signals": ["...", "..."],
  "support_levels": [price1, price2],
  "resistance_levels": [price1, price2]
}}
"""

    def _parse_llm_response(self, response_text: str) -> TechnicalBriefing:
        try:
            json_str = response_text.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(json_str)
        except Exception as e:
            print(f"Error parsing Technical JSON: {e}\nRaw: {response_text}")
            raise
