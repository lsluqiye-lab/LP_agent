"""
SentimentAnalyst Agent
"""
import json
import asyncio
from agent.schemas import SentimentBriefing, MarketSentiment
from llm.base import BaseLLM, ChatMessage, Role
from tools.search import get_search_client

class SentimentAnalyst:
    """
    The SentimentAnalyst analyzes market sentiment and news using Gemini Search.
    """

    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.search_client = get_search_client()

    async def analyze(self, symbol: str) -> SentimentBriefing:
        print(f"[{self.__class__.__name__}] Performing sentiment analysis for {symbol}...")

        # Step 1: Gather sentiment information
        queries = [
            f"{symbol} stock market sentiment news and social media buzz",
            f"{symbol} retail sentiment stocktwits reddit WallStreetBets",
            f"{symbol} latest major news events and catalysts"
        ]
        
        print(f"[{self.__class__.__name__}] Gathering sentiment via Gemini Search...")
        results = []
        for q in queries:
            res = self.search_client.search(q)
            results.append(res)
            await asyncio.sleep(1) # Be nice to the API
        
        search_context = ""
        for q, res in zip(queries, results):
            search_context += f"--- Search Results for '{q}' ---\n{res}\n\n"

        # Step 2: Interpret with LLM
        prompt = self._build_interpretation_prompt(symbol, search_context)
        
        messages = [
            ChatMessage(role=Role.SYSTEM, content="You are a sentiment analyst specializing in crowd psychology."),
            ChatMessage(role=Role.USER, content=prompt)
        ]
        
        print(f"[{self.__class__.__name__}] Synthesizing sentiment with {self.llm.model}...")
        response = self.llm.chat(messages)
        
        if not response.content:
            raise ValueError("LLM returned empty sentiment briefing.")

        # Step 3: Parse and return
        return self._parse_llm_response(response.content)

    def _build_interpretation_prompt(self, symbol: str, context: str) -> str:
        sentiment_options = ", ".join(f'"{s}"' for s in MarketSentiment.__args__)
        
        return f"""
Analyze the provided search results regarding sentiment for {symbol}.
Focus on identifying potential FOMO, extreme fear, or key news catalysts.

SEARCH RESULTS:
{context}

---
REQUIREMENTS:
1. Determine Market Sentiment: {sentiment_options}.
2. Provide a concise summary of the current mood.
3. List the most critical recent news items.

Format as a single JSON object:
{{
  "market_sentiment": "...",
  "summary": "...",
  "key_news": ["...", "..."]
}}
"""

    def _parse_llm_response(self, response_text: str) -> SentimentBriefing:
        try:
            json_str = response_text.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(json_str)
        except Exception as e:
            print(f"Error parsing Sentiment JSON: {e}\nRaw: {response_text}")
            raise
