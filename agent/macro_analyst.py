
"""
MacroAnalyst Agent - "Seeing the Essence Through Phenomena"
"""
import asyncio
import json
import logging
from typing import Dict, List
from llm.base import BaseLLM, ChatMessage, Role
from tools.search import get_search_client

logger = logging.getLogger("strategic_agent")

class MacroAnalyst:
    """
    The MacroAnalyst performs deep structural analysis of macroeconomic data.
    It focuses on Fed policy, employment quality (not just quantity), and inflation drivers.
    """

    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.search_client = get_search_client()

    async def analyze(self) -> Dict:
        logger.info("[MacroAnalyst] Performing deep structural macro analysis...")

        # Step 1: Gather structural macro information
        queries = [
            "latest US non-farm payrolls structural analysis: high-tech layoffs vs service growth",
            "Fed FOMC June 2026 interest rate path projection and dot plot expectations",
            "Bank of Japan June 2026 rate hike rumors and Yen carry trade impact on US tech",
            "Global Central Bank Week June 2026: BoJ and Fed policy synergy/divergence",
            "10-year Treasury yield impact on high-growth tech valuation logic"
        ]
        
        tasks = [asyncio.to_thread(self.search_client.search, q) for q in queries]
        results = await asyncio.gather(*tasks)
        
        search_context = ""
        for q, res in zip(queries, results):
            search_context += f"--- Macro Search for '{q}' ---\n{res}\n\n"

        # Step 2: Deep Reasoning with LLM
        prompt = f"""你是一个全球顶级对冲基金的**宏观战略研究员**。
你的任务是剥开表面数据的迷雾，为 CIO 提供“透过现象看本质”的宏观研判。

### 初始数据与搜索上下文：
{search_context}

### 你的分析指南：
1. **结构化分析 (Structural Analysis)**：不要只看非农或 GDP 的总量。分析其质量。例如：就业增长是否由低薪服务业驱动？高薪科技/金融行业是否在缩减？这如何影响长期购买力和估值？
2. **利率传导 (Rate Transmission)**：美联储的最新表态对长端利率有何影响？高利率对高 Beta/高 P/E 科技股（如 NVDA, TSM）的估值压制逻辑是否正在加强？
3. **预期差 (Expectation Gap)**：华尔街目前的误判在哪里？有什么是被大众忽略的深层利空或利好？

### 输出要求：
你必须返回一个结构化的 JSON 对象，包含以下字段：
- `headline`: 宏观标题。
- `essence_analysis`: 深度本质分析（核心逻辑，解释现象背后的真实博弈）。
- `structural_contradiction`: 发现的结构性矛盾（如就业好但高科技裁员）。
- `fed_path_risk`: 对美联储路径及利率风险的研判。
- `sector_impact`: 对我们重仓的科技/半导体板块的具体影响预估。
- `risk_warning`: 给 CIO 的核心风险警示。

请确保输出为纯净的 JSON。
"""
        
        messages = [
            ChatMessage(role=Role.SYSTEM, content="You are a macro economist specializing in deep reasoning and structural analysis."),
            ChatMessage(role=Role.USER, content=prompt)
        ]
        
        response = await asyncio.to_thread(self.llm.chat, messages)
        
        try:
            # 尝试提取 JSON
            import re
            json_match = re.search(r'(\{.*\})', response.content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            else:
                return {
                    "headline": "宏观数据分析异常",
                    "essence_analysis": response.content,
                    "risk_warning": "无法解析结构化研报，请 CIO 自行研判。"
                }
        except Exception as e:
            logger.error(f"MacroAnalyst JSON parsing failed: {e}")
            return {"error": "Macro analysis synthesis failed."}
