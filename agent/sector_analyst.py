"""
SectorAnalyst Agent
"""
import asyncio
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

    async def analyze(self, sector_rs: dict = None) -> SectorBriefing:
        logger.info(f"[{self.__class__.__name__}] Performing sector rotation analysis...")

        raw_data_map = {}
        for symbol, name in self.sector_etfs.items():
            try:
                # Synchronous tool call wrapped in to_thread
                raw_json = await asyncio.to_thread(self.tech_tool.execute, symbol=symbol)
                raw_data = json.loads(raw_json)
                if "error" not in raw_data:
                    raw_data_map[name] = raw_data
            except Exception as e:
                logger.warning(f"Failed to get data for {symbol} ({name}): {e}")

        if not raw_data_map and not sector_rs:
            return self._fallback_briefing()

        prompt = self._build_interpretation_prompt(raw_data_map, sector_rs)
        
        messages = [
            ChatMessage(role=Role.SYSTEM, content="You are a macro-sector rotation analyst. Output ONLY valid JSON matching the SectorBriefing schema. No markdown formatting, no explanations."),
            ChatMessage(role=Role.USER, content=prompt)
        ]
        
        try:
            response = await asyncio.to_thread(self.llm.chat, messages)
            content = response.content.strip()
            
            import re
            match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                json_str = match.group(1)
            else:
                match = re.search(r'(\{.*\})', content, re.DOTALL)
                json_str = match.group(1) if match else content
                
            data = json.loads(json_str.strip())
            return SectorBriefing(
                summary=data.get("summary", "Sector analysis completed."),
                strong_sectors=data.get("strong_sectors", []),
                weak_sectors=data.get("weak_sectors", []),
                risk_warning=data.get("risk_warning", "No specific sector risks identified.")
            )
        except Exception as e:
            logger.error(f"Failed to parse SectorAnalyst LLM response: {e}")
            return self._fallback_briefing()

    def _build_interpretation_prompt(self, raw_data_map: dict, sector_rs: dict = None) -> str:
        prompt = "Analyze the following data for major market sectors and identify money flow/rotation.\n\n"
        
        if sector_rs:
            prompt += "### 1. 行业数学相对强度 (Sector Relative Strength vs SPY - Realtime):\n"
            prompt += "(注：数值 > 1.0 代表跑赢大盘，数值越高代表行业动能越强)\n"
            for sector, rs in sector_rs.items():
                prompt += f"- {sector}: RS_Ratio={rs}\n"
            prompt += "\n"

        prompt += "### 2. 核心 ETF 技术面数据:\n"
        for sector, data in raw_data_map.items():
            rsi = data.get("RSI_14", "N/A")
            macd = data.get("MACD", {}).get("trend", "N/A")
            uptrend = data.get("uptrend", "N/A")
            price = data.get("price", "N/A")
            prompt += f"- {sector}: Price={price}, RSI={rsi}, MACD={macd}, Uptrend={uptrend}\n"
        
        prompt += """
### 3. 分析任务:
结合上述数学 RS 因子和技术指标，提供一个 JSON 对象：
{
  "summary": "简述当前资金在哪些板块聚集，哪些板块正在退潮。特别指出数学强度排名第一的行业。",
  "market_main_line": "当前最强的主线板块名称 (如: Technology, Energy等)",
  "strong_sectors": ["sector1", ...],
  "weak_sectors": ["sector2", ...],
  "risk_warning": "识别拥挤度风险（如RS很高但RSI极度超买）"
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