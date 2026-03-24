"""
宏观风控评分引擎
连续评分 0-100，自动调节交易审批门槛

评分维度（加权）:
  1. 市场温度 (25%) - LongPort market_temperature
  2. SPY技术面 (25%) - 均线、趋势、MACD
  3. RSI广度 (15%)   - 标的池整体RSI健康度
  4. 资金流向 (15%)   - 标的池主力资金方向
  5. 市场情绪 (10%)   - LongPort sentiment + 温度
  6. 波动率 (10%)     - ATR/振幅变化

风险级别:
  0-30:  LOCKDOWN  - 极端风险，禁止新建仓
  30-50: CAUTIOUS  - 高风险，仅允许减仓
  50-70: NORMAL    - 中性，正常但谨慎
  70-100: FAVORABLE - 低风险，可积极建仓
"""
import json
import logging
from typing import Optional
from datetime import datetime

import pytz

from config import RiskConfig, WATCHLIST
from data.trade_logger import get_trade_logger


logger = logging.getLogger("RiskManager")


class RiskRegime:
    """风险级别常量"""
    LOCKDOWN = "lockdown"
    CAUTIOUS = "cautious"
    NORMAL = "normal"
    FAVORABLE = "favorable"


class MacroRiskManager:
    """
    宏观风控评分管理器

    在每轮 ReAct 循环前运行，计算当前宏观环境评分，
    决定本轮是否允许新建仓、仓位上限等约束。
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()
        self.trade_logger = get_trade_logger()
        self._last_score: Optional[float] = None
        self._last_regime: Optional[str] = None
        self._last_components: Optional[dict] = None

    @property
    def last_score(self) -> Optional[float]:
        return self._last_score

    @property
    def last_regime(self) -> Optional[str]:
        return self._last_regime

    def calculate_risk_score(self, market_overview: dict) -> dict:
        """
        计算综合风控评分

        Args:
            market_overview: get_market_overview 工具返回的解析后 dict

        Returns:
            {
                "score": 0-100,
                "regime": "lockdown/cautious/normal/favorable",
                "components": {...},
                "constraints": {...},
            }
        """
        components = {}

        # ── 1. 市场温度评分 (0-100) ──
        components["market_temperature"] = self._score_market_temperature(market_overview)

        # ── 2. SPY技术面评分 (0-100) ──
        components["spy_technical"] = self._score_spy_technical(market_overview)

        # ── 3. RSI广度评分 (0-100) ──
        components["rsi_breadth"] = self._score_rsi_breadth(market_overview)

        # ── 4. 资金流向评分 (0-100) ──
        components["capital_flow"] = self._score_capital_flow(market_overview)

        # ── 5. 市场情绪评分 (0-100) ──
        components["sentiment"] = self._score_sentiment(market_overview)

        # ── 6. 波动率评分 (0-100) ──
        components["volatility"] = self._score_volatility(market_overview)

        # ── 加权汇总 ──
        cfg = self.config
        weighted_score = (
            components["market_temperature"] * cfg.weight_market_temp +
            components["spy_technical"] * cfg.weight_spy_technical +
            components["rsi_breadth"] * cfg.weight_rsi_breadth +
            components["capital_flow"] * cfg.weight_capital_flow +
            components["sentiment"] * cfg.weight_sentiment +
            components["volatility"] * cfg.weight_volatility
        )
        score = round(max(0, min(100, weighted_score)), 1)

        # ── 确定风险级别 ──
        if score < cfg.score_lockdown:
            regime = RiskRegime.LOCKDOWN
        elif score < cfg.score_cautious:
            regime = RiskRegime.CAUTIOUS
        elif score < cfg.score_normal:
            regime = RiskRegime.NORMAL
        else:
            regime = RiskRegime.FAVORABLE

        # ── 硬约束：SPY 技术面过弱时降级 ──
        # 经验教训（2026-03-23 复盘）：SPY 技术面 < 40 时即使总分 NORMAL 也不应新建仓
        spy_tech_score = components.get("spy_technical", 50)
        spy_override = False
        if spy_tech_score < 40 and regime in (RiskRegime.NORMAL, RiskRegime.FAVORABLE):
            regime = RiskRegime.CAUTIOUS
            spy_override = True
            logger.warning(
                f"SPY技术面硬约束触发: spy_technical={spy_tech_score:.0f} < 40, "
                f"总分 {score} 降级为 CAUTIOUS（禁止新建仓）"
            )

        # ── 计算约束条件 ──
        constraints = self._calculate_constraints(score, regime)

        # 如果被 SPY 硬约束降级，在 message 中标注
        if spy_override:
            constraints["spy_technical_override"] = True
            constraints["message"] = (
                f"CAUTIOUS (score={score}, SPY技术面={spy_tech_score:.0f}<40 触发硬约束): "
                f"SPY 极度弱势，禁止新建仓，仅允许减仓或持有"
            )

        # 缓存 & 记录
        self._last_score = score
        self._last_regime = regime
        self._last_components = components

        self.trade_logger.log_risk_score(score, components, regime)

        result = {
            "score": score,
            "regime": regime,
            "components": components,
            "constraints": constraints,
        }

        logger.info(f"风控评分: {score} ({regime}) | 约束: {constraints}")
        return result

    # ═══════════════════════════════════════
    # 各维度评分函数
    # ═══════════════════════════════════════

    def _score_market_temperature(self, data: dict) -> float:
        """
        市场温度评分

        LongPort temperature: 0-100（市场热度）
        我们希望温度适中（30-70为佳），过高过低都扣分
        """
        temp_data = data.get("market_temperature", {})
        if not temp_data or "error" in temp_data:
            return 50.0  # 无数据时给中性分

        temperature = temp_data.get("temperature")
        if temperature is None:
            return 50.0

        # 温度 40-60 最好(100分)，往两端递减
        if 40 <= temperature <= 60:
            return 100.0
        elif 30 <= temperature < 40 or 60 < temperature <= 70:
            return 75.0
        elif 20 <= temperature < 30 or 70 < temperature <= 80:
            return 50.0
        elif temperature < 20:
            return 25.0  # 极度冷淡
        else:
            return 30.0  # 极度过热

    def _score_spy_technical(self, data: dict) -> float:
        """
        SPY技术面评分

        多头排列=高分，均线收敛=中分，空头排列=低分
        """
        spy = data.get("indexes", {}).get("SPY", {})
        if not spy or "error" in spy:
            return 50.0

        score = 50.0  # 基础分

        # 趋势
        if spy.get("uptrend"):
            score += 25  # Stage 2 多头排列
        elif spy.get("price_above_SMA200"):
            score += 10  # 至少在200日线上方
        else:
            score -= 20  # 跌破200日线

        # RSI
        rsi = spy.get("RSI_14")
        if rsi:
            if 40 <= rsi <= 65:
                score += 15  # 健康区间
            elif 30 <= rsi < 40 or 65 < rsi <= 75:
                score += 5
            elif rsi > 80:
                score -= 10  # 超买
            elif rsi < 30:
                score -= 15  # 超卖/恐慌

        # MACD
        macd = spy.get("MACD")
        if macd:
            if macd.get("cross") == "golden_cross":
                score += 10
            elif macd.get("cross") == "death_cross":
                score -= 10
            elif macd.get("trend") == "bullish":
                score += 5
            else:
                score -= 5

        return max(0, min(100, score))

    def _score_rsi_breadth(self, data: dict) -> float:
        """
        RSI广度评分

        基于标的池扫描数据中的RSI分布
        多数股票RSI在40-70之间 = 健康
        """
        scan = data.get("watchlist_scan", [])
        if not scan:
            return 50.0

        healthy = 0
        overbought = 0
        oversold = 0
        total = 0

        for item in scan:
            rsi = item.get("rsi_14")
            if rsi is None:
                continue
            total += 1
            if 35 <= rsi <= 75:
                healthy += 1
            elif rsi > 75:
                overbought += 1
            else:
                oversold += 1

        if total == 0:
            return 50.0

        healthy_pct = healthy / total
        oversold_pct = oversold / total

        # 80%以上健康=90分，逐步递减
        score = healthy_pct * 100
        # 超卖占比高额外扣分
        score -= oversold_pct * 30

        return max(0, min(100, score))

    def _score_capital_flow(self, data: dict) -> float:
        """
        资金流向评分

        基于标的池扫描数据，使用涨幅加权评估资金方向。
        不再简单二分涨跌，而是考虑涨幅强度。
        """
        scan = data.get("watchlist_scan", [])
        if not scan:
            return 50.0

        weighted_sum = 0
        total = 0

        for item in scan:
            ret = item.get("return_20d_pct")
            if ret is None:
                continue
            total += 1
            # 将涨跌幅映射到 0-100:
            #   -20% → 0, 0% → 50, +20% → 100
            mapped = max(0, min(100, 50 + ret * 2.5))
            weighted_sum += mapped

        if total == 0:
            return 50.0

        return max(0, min(100, weighted_sum / total))

    def _score_sentiment(self, data: dict) -> float:
        """
        市场情绪评分

        LongPort sentiment: 0-100
        低=恐惧(可能见底)，高=贪婪(风险高)
        我们想要中性偏贪婪
        """
        temp_data = data.get("market_temperature", {})
        if not temp_data or "error" in temp_data:
            return 50.0

        sentiment = temp_data.get("sentiment")
        if sentiment is None:
            return 50.0

        # sentiment 40-70 最佳
        if 40 <= sentiment <= 70:
            return 90.0
        elif 30 <= sentiment < 40:
            return 65.0
        elif 70 < sentiment <= 80:
            return 60.0  # 偏贪婪，略降
        elif sentiment < 20:
            return 30.0  # 极度恐惧
        elif sentiment > 85:
            return 25.0  # 极度贪婪
        else:
            return 50.0

    def _score_volatility(self, data: dict) -> float:
        """
        波动率评分

        低波动 = 高分（稳定环境）
        高波动 = 低分（风险高）
        """
        spy = data.get("indexes", {}).get("SPY", {})
        if not spy or "error" in spy:
            return 50.0

        atr = spy.get("ATR_14")
        price = spy.get("price")

        if not atr or not price:
            return 50.0

        atr_pct = atr / price * 100

        # ATR% < 1 = 低波动(90分)，1-1.5 = 正常(70分)，1.5-2 = 偏高(50分)，>2 = 高波动(30分)
        if atr_pct < 0.8:
            return 95.0
        elif atr_pct < 1.2:
            return 80.0
        elif atr_pct < 1.5:
            return 65.0
        elif atr_pct < 2.0:
            return 45.0
        elif atr_pct < 3.0:
            return 30.0
        else:
            return 15.0

    # ═══════════════════════════════════════
    # 约束条件计算
    # ═══════════════════════════════════════

    def _calculate_constraints(self, score: float, regime: str) -> dict:
        """
        根据评分计算交易约束

        Returns:
            {
                "allow_new_buy": bool,
                "allow_add_position": bool,
                "max_single_position_pct": float,
                "max_total_position_pct": float,
                "position_multiplier": float,
                "message": str,
            }
        """
        cfg = self.config

        if regime == RiskRegime.LOCKDOWN:
            return {
                "allow_new_buy": False,
                "allow_add_position": False,
                "force_reduce": True,
                "max_single_position_pct": 0,
                "max_total_position_pct": cfg.base_total_position_pct * 0.3,
                "position_multiplier": 0.0,
                "message": f"LOCKDOWN (score={score}): 禁止一切新建仓，考虑减仓至30%以下",
            }
        elif regime == RiskRegime.CAUTIOUS:
            multiplier = (score - 30) / 20 * 0.3  # 30-50分 → 0-0.3倍
            return {
                "allow_new_buy": False,
                "allow_add_position": False,
                "force_reduce": False,
                "max_single_position_pct": cfg.base_max_position_pct * multiplier,
                "max_total_position_pct": cfg.base_total_position_pct * 0.5,
                "position_multiplier": round(multiplier, 2),
                "message": f"CAUTIOUS (score={score}): 仅允许减仓或持有，总仓位限制50%",
            }
        elif regime == RiskRegime.NORMAL:
            multiplier = 0.3 + (score - 50) / 20 * 0.5  # 50-70分 → 0.3-0.8倍
            return {
                "allow_new_buy": True,
                "allow_add_position": True,
                "force_reduce": False,
                "max_single_position_pct": round(cfg.base_max_position_pct * multiplier, 3),
                "max_total_position_pct": round(cfg.base_total_position_pct * (0.5 + multiplier * 0.3), 3),
                "position_multiplier": round(multiplier, 2),
                "message": f"NORMAL (score={score}): 可正常交易，仓位按 {multiplier:.0%} 执行",
            }
        else:  # FAVORABLE
            multiplier = 0.8 + (score - 70) / 30 * 0.2  # 70-100分 → 0.8-1.0倍
            multiplier = min(multiplier, 1.0)
            return {
                "allow_new_buy": True,
                "allow_add_position": True,
                "force_reduce": False,
                "max_single_position_pct": round(cfg.base_max_position_pct * multiplier, 3),
                "max_total_position_pct": round(cfg.base_total_position_pct * multiplier, 3),
                "position_multiplier": round(multiplier, 2),
                "message": f"FAVORABLE (score={score}): 环境良好，仓位按 {multiplier:.0%} 执行",
            }

    # ═══════════════════════════════════════
    # 审批接口
    # ═══════════════════════════════════════

    def approve_trade(self, action: str, symbol: str, amount_pct: float) -> dict:
        """
        审批一笔交易是否符合当前风控约束

        Args:
            action: "BUY" / "SELL" / "ADD"
            symbol: 股票代码
            amount_pct: 拟交易金额占总资产比例

        Returns:
            {
                "approved": bool,
                "reason": str,
                "adjusted_amount_pct": float,  # 风控调整后的仓位比例
            }
        """
        if self._last_score is None:
            return {
                "approved": False,
                "reason": "风控评分尚未计算，禁止交易",
                "adjusted_amount_pct": 0,
            }

        regime = self._last_regime
        score = self._last_score

        # 卖出始终允许
        if action == "SELL":
            return {
                "approved": True,
                "reason": f"卖出始终允许 (score={score}, regime={regime})",
                "adjusted_amount_pct": amount_pct,
            }

        # 检查是否在标的池内
        clean_symbol = symbol.replace(".US", "")
        if clean_symbol not in WATCHLIST:
            return {
                "approved": False,
                "reason": f"{clean_symbol} 不在标的池内，禁止交易",
                "adjusted_amount_pct": 0,
            }

        constraints = self._calculate_constraints(score, regime)

        if action == "BUY" and not constraints["allow_new_buy"]:
            return {
                "approved": False,
                "reason": constraints["message"],
                "adjusted_amount_pct": 0,
            }

        if action == "ADD" and not constraints["allow_add_position"]:
            return {
                "approved": False,
                "reason": constraints["message"],
                "adjusted_amount_pct": 0,
            }

        max_pct = constraints["max_single_position_pct"]
        adjusted = min(amount_pct, max_pct)

        if adjusted <= 0:
            return {
                "approved": False,
                "reason": f"风控约束下仓位为0 (multiplier={constraints['position_multiplier']})",
                "adjusted_amount_pct": 0,
            }

        return {
            "approved": True,
            "reason": f"通过审批 (score={score}, regime={regime}, multiplier={constraints['position_multiplier']})",
            "adjusted_amount_pct": round(adjusted, 4),
        }

    def get_risk_summary(self) -> str:
        """获取风控摘要文本，用于注入 LLM 上下文"""
        if self._last_score is None:
            return "风控状态: 未计算"

        constraints = self._calculate_constraints(self._last_score, self._last_regime)
        components = self._last_components or {}

        lines = [
            f"风控评分: {self._last_score}/100 ({self._last_regime.upper()})",
            f"  市场温度: {components.get('market_temperature', 'N/A')}",
            f"  SPY技术面: {components.get('spy_technical', 'N/A')}",
            f"  RSI广度: {components.get('rsi_breadth', 'N/A')}",
            f"  资金流向: {components.get('capital_flow', 'N/A')}",
            f"  市场情绪: {components.get('sentiment', 'N/A')}",
            f"  波动率: {components.get('volatility', 'N/A')}",
            f"约束: {constraints['message']}",
            f"  允许新建仓: {constraints['allow_new_buy']}",
            f"  仓位倍率: {constraints['position_multiplier']}",
            f"  单笔上限: {constraints['max_single_position_pct']:.1%}",
            f"  总仓位上限: {constraints['max_total_position_pct']:.1%}",
        ]
        return "\n".join(lines)


# 全局单例
_risk_manager: Optional[MacroRiskManager] = None


def get_risk_manager(config: Optional[RiskConfig] = None) -> MacroRiskManager:
    """获取风控管理器单例"""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = MacroRiskManager(config)
    return _risk_manager
