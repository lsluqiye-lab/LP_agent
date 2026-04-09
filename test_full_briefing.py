"""
Orchestration Test: Running all expert agents to produce a full DecisionBriefing.
"""
import asyncio
import pprint
import os
from datetime import datetime
from config import AppConfig
from llm.gemini import GeminiLLM
from agent.fundamental_analyst import FundamentalAnalyst
from agent.technical_analyst import TechnicalAnalyst
from agent.sentiment_analyst import SentimentAnalyst
from agent.schemas import DecisionBriefing, MacroBriefing

async def main():
    print("--- Starting Full Decision Briefing Orchestration ---")

    # 1. Setup Configuration and Model (Using Flash for speed and quota)
    target_model = "gemini-3-flash-preview"
    os.environ["GEMINI_MODEL"] = target_model
    app_cfg = AppConfig.from_env(llm_provider="gemini")
    api_key = os.getenv("GEMINI_API_KEY") or app_cfg.llm.api_key
    
    # Shared Flash LLM for all experts
    expert_llm = GeminiLLM(api_key=api_key, model=target_model)

    # 2. Instantiate all experts
    f_analyst = FundamentalAnalyst(llm=expert_llm)
    t_analyst = TechnicalAnalyst(llm=expert_llm)
    s_analyst = SentimentAnalyst(llm=expert_llm)

    symbol = "NVDA"

    # 3. Execute all analysis in PARALLEL
    print(f"\n[Orchestrator] Launching parallel analysis for {symbol}...")
    
    try:
        # We run them concurrently to save time
        results = await asyncio.gather(
            f_analyst.analyze(symbol),
            t_analyst.analyze(symbol),
            s_analyst.analyze(symbol)
        )
        
        fundamental_res, technical_res, sentiment_res = results

        # 4. Mock a Macro Briefing (Usually from MacroRiskManager)
        macro_res: MacroBriefing = {
            "risk_level": "NORMAL",
            "score": 65,
            "summary": "Market is stable, but watching upcoming inflation data.",
            "key_events": ["CPI next week"]
        }

        # 5. Assemble the final DecisionBriefing
        briefing: DecisionBriefing = {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "macro": macro_res,
            "fundamental": fundamental_res,
            "technical": technical_res,
            "sentiment": sentiment_res,
            "identified_conflicts": [] # Main agent will fill this, or we can pre-populate
        }

        # 6. Final logic: Preliminary Conflict Detection (Automated)
        conflicts = []
        if technical_res['trend_stage'] != "Stage 2":
            conflicts.append(f"Technical trend is {technical_res['trend_stage']}, not in buying zone (Stage 2).")
        
        if sentiment_res['market_sentiment'] == "Extreme Greed" and macro_res['risk_level'] == "CAUTIOUS":
            conflicts.append("Sentiment is Extreme Greed while Macro is Cautious.")
            
        briefing['identified_conflicts'] = conflicts

        # 7. Print the glorious final result
        print("\n" + "="*60)
        print(f" FINAL DECISION BRIEFING FOR {symbol} ".center(60, "="))
        print("="*60)
        pprint.pprint(briefing)
        print("="*60)

        print("\n✅ Full orchestration successful!")

    except Exception as e:
        print(f"\n❌ Orchestration failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
