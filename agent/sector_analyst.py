"""
SectorAnalyst Agent
"""
import json
import logging
from agent.schemas import SectorBriefing
from llm.base import BaseLLM, ChatMessage, Role
from tools.market_data import GetTechnicalAnalysisTool

logger = logging.getLogger("SectorAnalyst")

class SectorAnalyst:
    """
    The SectorAnalyst agent analyzes key ETFs to determine sector rotation and relative strength.
    """

    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.tech_tool = GetTechnicalAnalysisTool()
        # Key ETFs representing major sectors
        self.sector_etfs = {
            "SMH": "Semiconductors",
            "XLK": "Technology",
            "XLF": "Financials",
            "XBI": "Biotech",
            "XLE": "Energy"
        }

    async def analyze(self) -> SectorBriefing:
        logger.info(f"[{self.__class__.__name__}] Performing sector rotation analysis...")

        raw_data_map = {}
        for symbol, name in self.sector_etfs.items():
            try:
                # Synchronous tool call
                raw_json = self.tech_tool.execute(symbol=symbol)
                raw_data = json.loads(raw_json)
                if "error" not in raw_data:
                    raw_data_map[name] = raw_data
            except Exception as e:
                logger.warning(f"Failed to get data for {symbol} ({name}): {e}")

        if not raw_data_map:
            return self._fallback_briefing()

        prompt = self._build_interpretation_prompt(raw_data_map)
        
        messages = [
            ChatMessage(role=Role.SYSTEM, content="You are a macro-sector rotation analyst. Output ONLY valid JSON matching the SectorBriefing schema. No markdown formatting, no explanations."),
            ChatMessage(role=Role.USER, content=prompt)
        ]
        
        try:
            response = self.llm.chat(messages)
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:-3].strip()
            elif content.startswith("```"):
                content = content[3:-3].strip()
                
            data = json.loads(content)
            return SectorBriefing(
                summary=data.get("summary", "Sector analysis completed."),
                strong_sectors=data.get("strong_sectors", []),
                weak_sectors=data.get("weak_sectors", []),
                risk_warning=data.get("risk_warning", "No specific sector risks identified.")
            )
        except Exception as e:
            logger.error(f"Failed to parse SectorAnalyst LLM response: {e}")
            return self._fallback_briefing()

    def _build_interpretation_prompt(self, raw_data_map: dict) -> str:
        prompt = "Analyze the following technical data for major market sectors and identify money flow/rotation:\n\n"
        for sector, data in raw_data_map.items():
            rsi = data.get("RSI_14", "N/A")
            macd = data.get("MACD", {}).get("trend", "N/A")
            uptrend = data.get("uptrend", "N/A")
            price = data.get("price", "N/A")
            prompt += f"- {sector}: Price={price}, RSI={rsi}, MACD={macd}, Uptrend={uptrend}\n"
        
        prompt += """
Based on the above, provide a JSON object with:
{
  "summary": "Brief 1-2 sentence summary of sector money flow",
  "strong_sectors": ["sector1", ...],
  "weak_sectors": ["sector2", ...],
  "risk_warning": "Any extreme overbought/crowded warnings (e.g., if Semiconductors RSI > 75)"
}
"""
        return prompt

    def _fallback_briefing(self) -> SectorBriefing:
        return SectorBriefing(
            summary="Sector analysis unavailable.",
            strong_sectors=[],
            weak_sectors=[],
            risk_warning="Data unavailable."
        )
