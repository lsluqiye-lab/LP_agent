import os
from google import genai
from config import AppConfig

def list_models():
    app_cfg = AppConfig.from_env(llm_provider="gemini")
    api_key = os.getenv("GEMINI_API_KEY") or app_cfg.llm.api_key
    
    if not api_key:
        print("Error: GEMINI_API_KEY not found.")
        return

    client = genai.Client(api_key=api_key)
    print("Available Models:")
    for m in client.models.list():
        print(f"- {m.name}")

if __name__ == "__main__":
    list_models()
