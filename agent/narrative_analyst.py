"""
NarrativeAnalyst Agent - "Quantifying the Unquantifiable"
Detects market narrative shifts, thematic momentum, and sentiment density.
"""
import asyncio
import json
import logging
import re
from typing import Dict, List
from llm.base import BaseLLM, ChatMessage, Role
from tools.search import get_search_client
from agent.schemas import NarrativeBriefing

logger = logging.getLogger("strategic_agent")

class NarrativeAnalyst:
    """
    The NarrativeAnalyst focuses on 'Narrative Momentum'.
    It tracks what the market is 'talking about' and how those stories change.
    """

    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.search_client = get_search_client()

    async def analyze(self) -> NarrativeBriefing:
        logger.info("[NarrativeAnalyst] Detecting market narrative momentum and thematic shifts...")

        # Step 1: Broad Narrative Probing
        queries = [
            "current dominant US stock market narratives and themes July 2026",
            "emerging market risks and narrative shifts: from AI growth to recession or interest rate fatigue",
            "AI investment narrative: from infrastructure build-out to software ROI verification",
            "liquidity narratives: yen carry trade, quantitative tightening, and regional bank stress",
            "market sentiment density: top 3 trending topics on Wall Street and their sentiment delta"
        ]
        
        tasks = [asyncio.to_thread(self.search_client.search, q) for q in queries]
        results = await asyncio.gather(*tasks)
        
        search_context = ""
        for q, res in zip(queries, results):
            search_context += f"--- Narrative Probing for '{q}' ---\n{res}\n\n"

        # Step 2: Synthesis with LLM
        prompt = f"""你是一个顶级的**叙事量化分析师 (Narrative Analyst)**。
你的任务是量化市场正在“讲什么故事”，并识别这些故事的动能转向。叙事的转向往往领先于量价指标。

### 搜索上下文（叙事素材）：
{search_context}

### 你的分析框架：
1. **核心叙事识别 (Core Narratives)**：识别当前主导市场的 2-3 个核心叙事（如“AI 无限增长”、“流动性危机”、“衰退交易”）。
2. **叙事动能 (Narrative Momentum)**：这些故事是在增强还是在衰减？新出现的变数是什么？
3. **叙事密度与偏移 (Density & Shift)**：某个话题的讨论热度是否突然激增？这种偏移是利好还是利空？
4. **反向叙事 (Contrarian Narrative)**：有什么非主流但正在悄悄壮大的声音？

### 输出要求：
你必须返回一个结构化的 JSON 对象，包含以下字段：
- `headline`: 当前市场叙事总纲。
- `top_narratives`: 一个列表，包含 3 个核心叙事，每个叙事包含 `theme` (主题), `intensity` (强度 1-10), `delta` (变化方向：strengthening/weakening), `description` (简述)。
- `narrative_shift_warning`: 关键叙事偏移警告。如果某个危险叙事（如衰退或流动性危机）密度激增，必须指出。
- `sentiment_density`: 市场整体的情绪密度分布（如：60% 贪婪/AI，40% 担忧/高利率）。
- `impact_on_strategy`: 对 CIO 交易策略的直接启示（叙事层面的建议）。

请确保输出为纯净的 JSON。
"""
        
        messages = [
            ChatMessage(role=Role.SYSTEM, content="You are a narrative analyst specializing in quantifying financial themes and sentiment shifts."),
            ChatMessage(role=Role.USER, content=prompt)
        ]
        
        response = await asyncio.to_thread(self.llm.chat, messages)
        
        try:
            json_match = re.search(r'(\{.*\})', response.content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
                # 🆕 Robustness fix: Ensure narrative_shift_warning is always a string
                warning = data.get('narrative_shift_warning', "")
                if isinstance(warning, dict):
                    data['narrative_shift_warning'] = json.dumps(warning, ensure_ascii=False)
                return data
            else:
                return {
                    "headline": "叙事量化分析异常",
                    "top_narratives": [],
                    "narrative_shift_warning": "解析失败: " + response.content[:200],
                    "sentiment_density": "Unknown",
                    "impact_on_strategy": "无法解析结构化报告，请关注宏观研报。"
                }
        except Exception as e:
            logger.error(f"NarrativeAnalyst JSON parsing failed: {e}")
            return {
                "headline": "叙事分析解析错误",
                "top_narratives": [],
                "narrative_shift_warning": f"Parsing Error: {str(e)}",
                "sentiment_density": "Error",
                "impact_on_strategy": "System Error."
            }
