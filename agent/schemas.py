import asyncio
from typing import TypedDict, List, Literal

# --- Reusable Enums for Clarity ---
RiskLevel = Literal["LOCKDOWN", "CAUTIOUS", "NORMAL", "FAVORABLE"]
TrendStage = Literal["Stage 1", "Stage 2", "Stage 3", "Stage 4", "Unknown"]
Valuation = Literal["Very Undervalued", "Undervalued", "Fair Value", "Overvalued", "Very Overvalued"]
MarketSentiment = Literal["Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"]
OwnershipTrend = Literal["Increasing", "Decreasing", "Stable"]

# --- Individual Expert Briefings ---

class MacroBriefing(TypedDict):
    """宏观环境分析"""
    risk_level: RiskLevel
    score: int  # 0-100
    summary: str  # e.g., "今晚有CPI数据，市场波动可能加剧"
    key_events: List[str]  # e.g., ["CPI Report (20:30 ET)", "Fed Chairman Speech (Tomorrow)"]

class TechnicalBriefing(TypedDict):
    """技术面分析"""
    trend_stage: TrendStage
    summary: str  # e.g., "处于强势上升趋势，但RSI指标显示超买"
    volume_price_analysis: str  # e.g., "放量突破，OBV看多背离，资金流入明显"
    is_volume_breakout: bool # e.g., True if volume is > 1.5x average
    breakout_quality_score: int # 0-100 score for breakout quality
    is_rsi_overbought: bool  # e.g., True if daily RSI > 75
    key_signals: List[str]  # e.g., ["RSI > 75", "股价 > 50日均线", "出现口袋支点"]
    support_levels: List[float]
    resistance_levels: List[float]

class FundamentalBriefing(TypedDict):
    """基本面分析"""
    valuation: Valuation
    summary: str  # e.g., "盈利增长强劲，但市盈率高于行业平均水平"
    strengths: List[str]  # e.g., ["AI芯片市场领导者", "营收同比增长 > 50%"]
    weaknesses: List[str]  # e.g., ["高度依赖单一供应商", "面临AMD的潜在竞争"]
    institutional_ownership_trend: OwnershipTrend

class SentimentBriefing(TypedDict):
    """市场情绪/新闻分析"""
    market_sentiment: MarketSentiment
    summary: str  # e.g., "社交媒体情绪极度看涨，新闻面利好"
    key_news: List[str]  # e.g., ["发布新一代Blackwell GPU", "多家投行上调目标价"]


class SectorBriefing(TypedDict):
    """板块与资金轮动分析"""
    summary: str  # e.g., "资金从科技向金融轮动，半导体高位滞涨"
    strong_sectors: List[str]
    weak_sectors: List[str]
    risk_warning: str  # e.g., "半导体板块拥挤度极高，警惕集中回调"

# --- The Final Assembled Briefing for ReAct Agent ---

class DecisionBriefing(TypedDict):
    """
    最终提交给主 ReAct Agent 的决策简报
    """
    symbol: str
    timestamp: str  # ISO 8601 format
    
    # 专家分析模块
    macro: MacroBriefing
    sector: SectorBriefing
    technical: TechnicalBriefing
    fundamental: FundamentalBriefing
    sentiment: SentimentBriefing
    
    # 核心：由预处理器或主Agent初步识别的矛盾点
    # This is the most critical field for the new ReAct loop.
    identified_conflicts: List[str]
    # e.g., [
    #   "基本面强劲 vs. 技术面超买",
    #   "市场情绪极度乐观 vs. 宏观环境谨慎"
    # ]
    
    # 专家层提纯的结构化确定性信号 (Information Distillation)
    identified_certainties: List[str]
    # e.g., [
    #   "真实量价齐升：成交量放大 > 1.5x",
    #   "明确的 Stage 2 主升浪"
    # ]
    
    # 该股票在本账户的近期历史战绩（胜率/盈亏等），用于长效记忆反思
    trading_history: str