"""
FundamentalAnalyst Agent with 5-Day Cache
"""
import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import List
from agent.schemas import FundamentalBriefing, Valuation, OwnershipTrend
from llm.base import BaseLLM, ChatMessage, Role
from tools.search import get_search_client

CACHE_FILE = "data/fundamental_cache.json"
CACHE_EXPIRY_DAYS = 5

class FundamentalAnalyst:
    """
    The FundamentalAnalyst agent is responsible for analyzing the fundamental aspects of a stock.
    It uses GeminiSearchClient to gather information and a BaseLLM to synthesize it.
    It features a 5-day local caching mechanism to prevent unnecessary LLM and search calls.
    """

    def __init__(self, llm: BaseLLM):
        """
        Initializes the FundamentalAnalyst with a BaseLLM instance.
        """
        self.llm = llm
        self.search_client = get_search_client()

    async def analyze(self, symbol: str) -> FundamentalBriefing:
        """
        Analyzes the fundamentals of a given stock symbol, utilizing 5-day cache if available.
        """
        print(f"[{self.__class__.__name__}] Starting fundamental analysis for {symbol}...")

        # Step 0: Check Local 5-Day Cache
        cache_data = self._load_cache()
        if symbol in cache_data:
            entry = cache_data[symbol]
            cached_time_str = entry.get("timestamp")
            if cached_time_str:
                try:
                    cached_time = datetime.fromisoformat(cached_time_str)
                    if datetime.now() - cached_time < timedelta(days=CACHE_EXPIRY_DAYS):
                        print(f"[{self.__class__.__name__}] ✅ Found active cache (created at {cached_time_str}) for {symbol}. Returning cached briefing to save tokens.")
                        return entry["data"]
                except Exception as e:
                    print(f"[{self.__class__.__name__}] Error reading cache timestamp for {symbol}: {e}")

        # Step 1: Gather information (Cache expired or missing)
        print(f"[{self.__class__.__name__}] Cache miss/expired. Performing search and LLM synthesis...")
        queries = [
            f"{symbol} valuation metrics (PE, PS, PEG) and peer comparison",
            f"{symbol} latest earnings report summary and future guidance",
            f"{symbol} core business strengths and competitive weaknesses",
            f"{symbol} institutional ownership trends and major stakeholder changes"
        ]
        
        print(f"[{self.__class__.__name__}] Gathering information via Gemini Search...")
        tasks = [asyncio.to_thread(self.search_client.search, q) for q in queries]
        results = await asyncio.gather(*tasks)
        
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
        
        response = await asyncio.to_thread(self.llm.chat, messages)
        
        if not response.content:
            raise ValueError("LLM returned an empty response.")

        # Step 4: Parse and structure the output
        print(f"[{self.__class__.__name__}] Parsing LLM response...")
        briefing = self._parse_llm_response(response.content)
        
        # Step 5: Save to Cache
        self._save_cache(symbol, briefing)
        
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

    def _load_cache(self) -> dict:
        """Loads cache from local JSON file."""
        if not os.path.exists(CACHE_FILE):
            return {}
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading fundamental cache file: {e}")
            return {}

    def _save_cache(self, symbol: str, data: dict):
        """Saves analysis data into local cache JSON file."""
        cache = self._load_cache()
        cache[symbol] = {
            "timestamp": datetime.now().isoformat(),
            "data": data
        }
        try:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=4, ensure_ascii=False)
            print(f"[{self.__class__.__name__}] Cached fundamental analysis for {symbol} to {CACHE_FILE}")
        except Exception as e:
            print(f"Error saving fundamental cache file: {e}")
