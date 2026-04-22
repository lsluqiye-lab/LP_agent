from llm.gemini import GeminiLLM
from llm.base import ChatMessage, Role
import os
from config import AppConfig

config = AppConfig.from_env(llm_provider="gemini")
print(f"Primary model: {config.llm.model}")
try:
    llm = GeminiLLM(
        api_key=config.llm.api_key,
        model=config.llm.model,
        fallback_model=config.llm.fallback_model
    )
    resp = llm.chat([ChatMessage(role=Role.USER, content="Hello")])
    print(f"Primary Response: {resp.content}")
except Exception as e:
    print(f"Primary LLM Error: {e}")

print(f"Analyst model: {config.analyst_llm.model}")
try:
    llm2 = GeminiLLM(
        api_key=config.analyst_llm.api_key,
        model=config.analyst_llm.model,
        fallback_model=config.analyst_llm.fallback_model
    )
    resp2 = llm2.chat([ChatMessage(role=Role.USER, content="Hello")])
    print(f"Analyst Response: {resp2.content}")
except Exception as e:
    print(f"Analyst LLM Error: {e}")
