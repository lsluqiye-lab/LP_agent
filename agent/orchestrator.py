"""
Expert Orchestrator
Handles parallel execution of specialized analyst agents.
"""
import asyncio
import logging
from typing import List, Dict
from datetime import datetime

from agent.schemas import DecisionBriefing, MacroBriefing
from agent.fundamental_analyst import FundamentalAnalyst
from agent.technical_analyst import TechnicalAnalyst
from agent.sentiment_analyst import SentimentAnalyst
from llm.base import BaseLLM

logger = logging.getLogger("Orchestrator")

class ExpertOrchestrator:
    """
    Coordinates specialized agents to produce a unified DecisionBriefing.
    """

    def __init__(self, llm: BaseLLM):
        """
        Initializes the orchestrator with a specialized LLM (typically a faster Flash model).
        """
        self.f_analyst = FundamentalAnalyst(llm)
        self.t_analyst = TechnicalAnalyst(llm)
        self.s_analyst = SentimentAnalyst(llm)
        # 实盘限流：限制同时分析的个股数量
        self.semaphore = asyncio.Semaphore(3)

    async def get_full_briefing(self, symbol: str, macro_briefing: MacroBriefing) -> DecisionBriefing:
        """
        Runs all expert agents in parallel and assembles the results.
        """
        try:
            async with self.semaphore:
                logger.info(f"Starting multi-expert analysis for {symbol}...")
                
                try:
                    # 给 API 调用留出一点点喘息时间，避免瞬间突发请求
                    await asyncio.sleep(0.5)
                    
                    # Parallel execution
                    results = await asyncio.gather(
                        self.f_analyst.analyze(symbol),
                        self.t_analyst.analyze(symbol),
                        self.s_analyst.analyze(symbol),
                        return_exceptions=True
                    )
                except Exception as e:
                    logger.error(f"Async gather failed for {symbol}: {e}")
                    results = [e, e, e]
                
                # Handle potential failures gracefully
                fundamental_res = results[0] if not isinstance(results[0], Exception) else self._get_error_briefing("Fundamental")
                technical_res = results[1] if not isinstance(results[1], Exception) else self._get_error_briefing("Technical")
                sentiment_res = results[2] if not isinstance(results[2], Exception) else self._get_error_briefing("Sentiment")

                if any(isinstance(r, Exception) for r in results):
                    for r in results:
                        if isinstance(r, Exception):
                            logger.error(f"Expert analysis failed: {r}")

                # Assemble
                briefing: DecisionBriefing = {
                    "symbol": symbol,
                    "timestamp": datetime.now().isoformat(),
                    "macro": macro_briefing,
                    "fundamental": fundamental_res,
                    "technical": technical_res,
                    "sentiment": sentiment_res,
                    "identified_conflicts": self._detect_conflicts(macro_briefing, fundamental_res, technical_res, sentiment_res)
                }
                
                return briefing

        except Exception as e:
            logger.error(f"Orchestration failed for {symbol}: {e}")
            raise

    def _detect_conflicts(self, macro, fundamental, technical, sentiment) -> List[str]:
        """
        Heuristic-based preliminary conflict detection to prime the main agent.
        """
        conflicts = []
        
        # 1. Fundamental vs Technical
        if fundamental.get('valuation') in ["Undervalued", "Very Undervalued"] and technical.get('trend_stage') == "Stage 4":
             conflicts.append("Fundamental Value vs Technical Stage 4 (Downtrend). Extreme divergence.")
        
        # 2. Sentiment vs Macro
        if sentiment.get('market_sentiment') in ["Greed", "Extreme Greed"] and macro.get('risk_level') in ["LOCKDOWN", "CAUTIOUS"]:
            conflicts.append("Market FOMO/Greed vs Macro Risk constraints.")
            
        # 3. Stage 2 Constraint
        if technical.get('trend_stage') != "Stage 2":
            conflicts.append(f"Price is in {technical.get('trend_stage')}, failing the Stage 2 buy requirement.")

        # 4. Momentum vs Volume
        # (Add more as needed)

        return conflicts

    def _get_error_briefing(self, expert_name: str) -> Dict:
        """Fallback for failed analysis."""
        return {
            "error": f"{expert_name} analysis failed.",
            "summary": "Data unavailable due to expert error.",
            "valuation": "Unknown",
            "trend_stage": "Unknown",
            "market_sentiment": "Neutral",
            "key_signals": [],
            "strengths": [],
            "weaknesses": []
        }
