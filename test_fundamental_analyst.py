"""
Verification script for the FundamentalAnalyst agent.
"""
import asyncio
import pprint
import os
from config import AppConfig
from llm.gemini import GeminiLLM
from agent.fundamental_analyst import FundamentalAnalyst
from agent.schemas import FundamentalBriefing

async def main():
    """
    Main function to run the verification test.
    """
    print("--- Starting FundamentalAnalyst Verification (Flash Model) ---")

    # 1. Set the model to Flash as requested by the user
    # Using the suggested model name
    target_model = "gemini-3-flash-preview" # Confirmed available flash model
    os.environ["GEMINI_MODEL"] = target_model
    print(f"Using model: {target_model}")

    # Load configuration
    app_cfg = AppConfig.from_env(llm_provider="gemini")
    
    # Ensure API Key for search and LLM is present
    api_key = os.getenv("GEMINI_API_KEY") or app_cfg.llm.api_key
    if not api_key:
        raise ValueError("GEMINI_API_KEY must be set in your .env file or environment.")
    
    os.environ["GEMINI_API_KEY"] = api_key
        
    # Initialize with Flash model
    gemini_llm = GeminiLLM(
        api_key=api_key,
        model=target_model
    )

    # 2. Instantiate the analyst agent
    analyst = FundamentalAnalyst(llm=gemini_llm)

    # 3. Define the target symbol for analysis
    symbol_to_test = "NVDA"

    # 4. Run the analysis
    try:
        fundamental_briefing: FundamentalBriefing = await analyst.analyze(symbol_to_test)

        # 5. Print result
        print("\n--- Verification Result: Structured Fundamental Briefing ---")
        pprint.pprint(fundamental_briefing)
        print("----------------------------------------------------------")

        # 6. Basic assertions
        assert isinstance(fundamental_briefing, dict)
        assert "valuation" in fundamental_briefing
        assert "summary" in fundamental_briefing
        assert "strengths" in fundamental_briefing
        assert "weaknesses" in fundamental_briefing
        assert "institutional_ownership_trend" in fundamental_briefing

        print("\n✅ All assertions passed successfully!")
        print("--- FundamentalAnalyst Verification Complete ---")

    except Exception as e:
        print(f"\n❌ Verification failed with an error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
