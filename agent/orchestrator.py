"""
Expert Orchestrator
Handles parallel execution of specialized analyst agents.
"""
import asyncio
import logging
from typing import List, Dict
from datetime import datetime

from agent.schemas import DecisionBriefing, MacroBriefing, SectorBriefing, NarrativeBriefing
from agent.fundamental_analyst import FundamentalAnalyst
from agent.technical_analyst import TechnicalAnalyst
from agent.sentiment_analyst import SentimentAnalyst
from agent.sector_analyst import SectorAnalyst
from agent.macro_analyst import MacroAnalyst
from agent.narrative_analyst import NarrativeAnalyst
from llm.base import BaseLLM
from logger import setup_logger

logger = setup_logger("strategic_agent")

class ExpertOrchestrator:
    """
    Coordinates specialized agents to produce a unified DecisionBriefing.
    """

    def __init__(self, llm: BaseLLM, feishu_notifier=None):
        """
        Initializes the orchestrator with a specialized LLM (typically a faster Flash model).
        """
        self.f_analyst = FundamentalAnalyst(llm)
        self.t_analyst = TechnicalAnalyst(llm)
        self.s_analyst = SentimentAnalyst(llm)
        self.sec_analyst = SectorAnalyst(llm)
        self.m_analyst = MacroAnalyst(llm)
        self.n_analyst = NarrativeAnalyst(llm)
        self.feishu_notifier = feishu_notifier

    async def get_sector_briefing(self) -> SectorBriefing:
        """Runs the sector rotation analysis once globally."""
        return await self.sec_analyst.analyze()

    async def get_macro_deep_briefing(self) -> Dict:
        """Runs the deep macro structural analysis."""
        return await self.m_analyst.analyze()

    async def get_narrative_briefing(self) -> NarrativeBriefing:
        """Runs the global narrative momentum analysis."""
        return await self.n_analyst.analyze()

    async def get_full_briefing(self, symbol: str, macro_briefing: MacroBriefing, sector_briefing: SectorBriefing, narrative_briefing: NarrativeBriefing = None) -> DecisionBriefing:
        """
        Runs all expert agents in parallel and assembles the results.
        """
        logger.info(f"Starting multi-expert analysis for {symbol}...")
        
        try:
            # Parallel execution
            results = await asyncio.gather(
                self.f_analyst.analyze(symbol),
                self.t_analyst.analyze(symbol),
                self.s_analyst.analyze(symbol),
                return_exceptions=True
            )
            
            # Handle potential failures gracefully
            fundamental_res = results[0] if not isinstance(results[0], Exception) else self._get_error_briefing("Fundamental")
            technical_res = results[1] if not isinstance(results[1], Exception) else self._get_error_briefing("Technical")
            sentiment_res = results[2] if not isinstance(results[2], Exception) else self._get_error_briefing("Sentiment")

            if any(isinstance(r, Exception) for r in results):
                for r in results:
                    if isinstance(r, Exception):
                        logger.error(f"Expert analysis failed: {r}")

            # --- Extract historical stats if trade_logger exists ---
            history_summary = "无该标的近期交易历史。"
            try:
                from data.trade_logger import get_trade_logger
                trade_log = get_trade_logger()
                recent_logs = trade_log.get_recent_logs(days=14)
                trade_count = 0
                for log in recent_logs:
                    for t in log.get("trades", []):
                        if t.get("symbol") == symbol:
                            trade_count += 1
                if trade_count > 0:
                    history_summary = f"近14天内对 {symbol} 进行过 {trade_count} 笔交易。请复盘是否陷入频繁买卖或反复止损陷阱，如果屡战屡败请避开。"
            except Exception as e:
                logger.error(f"获取 {symbol} 的交易历史失败: {e}")

            # --- Extract quantitative metadata from watchlist ---
            quant_metadata = {}
            try:
                import json
                import os
                watchlist_path = "data/daily_watchlist.json"
                if os.path.exists(watchlist_path):
                    with open(watchlist_path, "r", encoding="utf-8") as f:
                        w_data = json.load(f)
                        details = w_data.get("watchlist_detail", {})
                        if symbol in details:
                            quant_metadata = details[symbol]
                            logger.info(f"Loaded quant metadata for {symbol}: {quant_metadata}")
            except Exception as e:
                logger.error(f"Failed to load quant metadata for {symbol}: {e}")

            # Assemble
            briefing: DecisionBriefing = {
                "symbol": symbol,
                "timestamp": datetime.now().isoformat(),
                "macro": macro_briefing,
                "narrative": narrative_briefing or self._get_empty_narrative(),
                "sector": sector_briefing,
                "fundamental": fundamental_res,
                "technical": technical_res,
                "sentiment": sentiment_res,
                "quant_metadata": quant_metadata,
                "identified_conflicts": self._detect_conflicts(macro_briefing, fundamental_res, technical_res, sentiment_res, history_summary, narrative_briefing),
                "identified_certainties": self._detect_certainties(macro_briefing, fundamental_res, technical_res, sentiment_res),
                "trading_history": history_summary
            }
            
            # --- Whipsaw Cool-down Injection (v4.4.2) ---
            try:
                from agent.portfolio_manager import PortfolioManager
                pm = PortfolioManager()
                # 确保 score 是数值
                try:
                    safe_score = float(macro_briefing.get("score", 50))
                except (ValueError, TypeError, AttributeError):
                    safe_score = 50.0
                
                # 模拟一个 PM 报告来获取黑名单
                pm_report = pm.analyze_portfolio([], {}, {"score": safe_score})
                for weed in pm_report.get("recently_weeded_out", []):
                    if weed["symbol"] == symbol:
                        cool_down_msg = f"🚨 止损冷静期拦截 ({weed['type']}): 该标的最近刚被止损/淘汰，目前处于保护禁买期。除非发生极罕见的、放量 2x 以上且收复 50% 跌幅的强力 V-Recovery，否则严禁买入！"
                        briefing["identified_conflicts"].append(cool_down_msg)
            except Exception as e:
                logger.error(f"Failed to inject cool-down conflict for {symbol}: {e}")

            
            # --- Send Live Broadcast Card to Feishu ---
            if self.feishu_notifier:
                t_summary = technical_res.get('summary', '无')
                f_summary = fundamental_res.get('summary', '无')
                s_summary = sentiment_res.get('summary', '无')
                
                # Format a nice markdown card
                card_content = (
                    f"**📈 技术面 ({technical_res.get('trend_stage', 'Unknown')}):**\\n{t_summary}\\n\\n"
                    f"**🏢 基本面 ({fundamental_res.get('valuation', 'Unknown')}):**\\n{f_summary}\\n\\n"
                    f"**🌐 消息面 ({sentiment_res.get('market_sentiment', 'Unknown')}):**\\n{s_summary}"
                )
                
                if briefing.get('identified_certainties'):
                    certainty_str = "\\n- ".join(briefing['identified_certainties'])
                    card_content += f"\\n\\n**✅ 确认信号 (Certainties):**\\n- {certainty_str}"

                if briefing.get('identified_conflicts'):
                    conflict_str = "\\n- ".join(briefing['identified_conflicts'])
                    card_content += f"\\n\\n**⚠️ 矛盾/警报 (Conflicts):**\\n- {conflict_str}"

                self.feishu_notifier.send_card(
                    title=f"🔎 专家研报完成: {symbol}",
                    content=card_content,
                    color="blue",
                    footer="Phase 3: Expert Analysis"
                )
            
            return briefing

        except Exception as e:
            logger.error(f"Orchestration failed for {symbol}: {e}")
            raise

    def _detect_conflicts(self, macro, fundamental, technical, sentiment, history_summary="", narrative: NarrativeBriefing = None) -> List[str]:
        """
        Heuristic-based preliminary conflict detection to prime the main agent.
        """
        conflicts = []
        
        # 0. Narrative Conflict (v4.5 NME)
        if narrative and narrative.get('narrative_shift_warning'):
            warning = narrative['narrative_shift_warning']
            if any(k in warning.lower() for k in ["风险", "转向", "激增", "危机", "bubble", "risk", "warning", "stress", "crash"]):
                conflicts.append(f"⚠️ 叙事偏移警告：{warning}")

        # 0. High Frequency Warning (v4.4.2)
        if "频繁交易" in history_summary or "反复止损" in history_summary:
            conflicts.append("⚠️ 警报：检测到近期对该标的进行过高频反复交易。请审视是否陷入过度交易陷阱，当前应提高建仓门槛或直接跳过。")
        
        # 1. Fundamental vs Technical
        if fundamental.get('valuation') in ["Undervalued", "Very Undervalued"] and technical.get('trend_stage') == "Stage 4":
             conflicts.append("Fundamental Value vs Technical Stage 4 (Downtrend). Extreme divergence.")
        
        # 2. Sentiment vs Macro
        if sentiment.get('market_sentiment') in ["Greed", "Extreme Greed"] and macro.get('risk_level') in ["LOCKDOWN", "CAUTIOUS"]:
            conflicts.append("Market FOMO/Greed vs Macro Risk constraints.")
            
        # 3. Stage 2 Constraint
        if technical.get('trend_stage') != "Stage 2":
            conflicts.append(f"Price is in {technical.get('trend_stage')}, failing the Stage 2 buy requirement.")

        # 4. Momentum vs Volume (Overbought Warning)
        if technical.get('is_rsi_overbought'):
            conflicts.append("⚠️ 技术指标极端超买 (RSI > 75). 警惕追高回调风险！")

        return conflicts

    def _detect_certainties(self, macro, fundamental, technical, sentiment) -> List[str]:
        """
        Extract strong positive confluences and structured indicators.
        """
        certainties = []

        # 1. Volume Confirmation
        if technical.get('is_volume_breakout'):
            certainties.append("✅ 真实量价齐升：监测到成交量显著异动放大 (>1.5x)。")
        
        # 2. Stage 2 Confirmation
        if technical.get('trend_stage') == "Stage 2":
             certainties.append("✅ 趋势确认：处于健康的 Stage 2 上升通道。")
             
        # 3. Fundamental Backing
        if fundamental.get('valuation') in ["Undervalued", "Very Undervalued", "Fair Value"]:
             certainties.append("✅ 基本面支撑：估值处于合理或低估区间。")
             
        return certainties

    def _get_empty_narrative(self) -> NarrativeBriefing:
        """Empty fallback for narrative briefing."""
        return {
            "headline": "无活跃叙事数据",
            "top_narratives": [],
            "narrative_shift_warning": "",
            "sentiment_density": "Unknown",
            "impact_on_strategy": "N/A"
        }

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