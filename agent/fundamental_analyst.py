"""
FundamentalAnalyst Agent
"""
import json
import asyncio
from typing import List
from agent.schemas import FundamentalBriefing, Valuation, OwnershipTrend
from llm.base import BaseLLM, ChatMessage, Role
from tools.search import get_search_client

class FundamentalAnalyst:
    """
    The FundamentalAnalyst agent is responsible for analyzing the fundamental aspects of a stock.
    It uses GeminiSearchClient to gather information and a BaseLLM to synthesize it.
    """

    def __init__(self, llm: BaseLLM):
        """
        Initializes the FundamentalAnalyst with a BaseLLM instance.
        """
        self.llm = llm
        self.search_client = get_search_client()

    async def analyze(self, symbol: str) -> FundamentalBriefing:
        """
        Analyzes the fundamentals of a given stock symbol.
        """
        print(f"[{self.__class__.__name__}] Starting fundamental analysis for {symbol}...")

        # Step 1: Gather information
        # We use a single, comprehensive search query to get the best out of Gemini Search
        # or multiple queries if needed. Here we combine them for efficiency.
        queries = [
            f"{symbol} valuation metrics (PE, PS, PEG) and peer comparison",
            f"{symbol} latest earnings report summary and future guidance",
            f"{symbol} core business strengths and competitive weaknesses",
            f"{symbol} institutional ownership trends and major stakeholder changes"
        ]
        
        print(f"[{self.__class__.__name__}] Gathering information via Gemini Search...")
        
        # We perform searches. Since GeminiSearchClient.search is synchronous but we are in an async method,
        # we can use run_in_executor to keep it non-blocking if needed, but for simplicity here we call it directly.
        # To avoid rate limits (15 RPM for free tier), we do them sequentially or with small delays.
        results = []
        for q in queries:
            res = await asyncio.to_thread(self.search_client.search, q)
            results.append(res)
            # Small sleep to be nice to the API
            await asyncio.sleep(1)
        
        search_context = ""
        for q, res in zip(queries, results):
            search_context += f"--- Search Results for '{q}' ---\n{res}\n\n"

        # Step 2: Construct the synthesis prompt
        prompt = self._build_synthesis_prompt(symbol, search_context)

        # Step 3: Call the LLM (using the chat method as required by BaseLLM)
        print(f"[{self.__class__.__name__}] Synthesizing information with {self.llm.get_provider_name()}...")
        
        messages = [
            ChatMessage(role=Role.SYSTEM, content="You are a senior US stock market fundamental analyst."),
            ChatMessage(role=Role.USER, content=prompt)
        ]
        
        import asyncio
        response = await asyncio.to_thread(self.llm.chat, messages)
        
        if not response.content:
            raise ValueError("LLM returned an empty response.")

        # Step 4: Parse and structure the output
        print(f"[{self.__class__.__name__}] Parsing LLM response...")
        briefing = self._parse_llm_response(response.content)
        
        print(f"[{self.__class__.__name__}] Fundamental analysis for {symbol} complete.")
        return briefing

    def _build_synthesis_prompt(self, symbol: str, search_context: str) -> str:
        """Builds the prompt for the LLM to synthesize the fundamental analysis."""
        
        valuation_options = ", ".join(f'"{v}"' for v in Valuation.__args__)
        ownership_options = ", ".join(f'"{o}"' for o in OwnershipTrend.__args__)

        return f"""
Provide a structured fundamental briefing for {symbol} based ONLY on these search results:

{search_context}

---
REQUIREMENTS:
1. Determine valuation: {valuation_options}
2. Determine institutional ownership trend: {ownership_options}
3. Summarize key findings.
4. List strengths and weaknesses.

Format as a single JSON object:
{{
  "valuation": "...",
  "summary": "...",
  "strengths": ["...", "..."],
  "weaknesses": ["...", "..."],
  "institutional_ownership_trend": "..."
}}
"""

    def _parse_llm_response(self, response_text: str) -> FundamentalBriefing:
        """Parses the JSON response and validates its structure."""
        import re
        try:
            # Clean possible markdown formatting
            match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
            if match:
                json_str = match.group(1)
            else:
                match = re.search(r'(\{.*\})', response_text, re.DOTALL)
                json_str = match.group(1) if match else response_text
            
            data = json.loads(json_str.strip())

            # Validate keys
            required_keys = ["valuation", "summary", "strengths", "weaknesses", "institutional_ownership_trend"]
            for key in required_keys:
                if key not in data:
                    raise ValueError(f"Missing key: {key}")

            return data
        except Exception as e:
            print(f"Error parsing JSON: {e}\nRaw Response: {response_text}")
            raise
