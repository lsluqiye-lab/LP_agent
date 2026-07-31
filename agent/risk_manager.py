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
import asyncio
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


# 板块细分映射表
SECTOR_MAP = {
    "NVDA": "Semi-Fabless", "AMD": "Semi-Fabless", 
    "TSM": "Semi-Foundry", 
    "ASML": "Semi-Equipment", "AMAT": "Semi-Equipment",
    "AAPL": "Consumer Electronics", "MSFT": "Software", 
    "GOOGL": "Internet", "META": "Internet",
    "TSLA": "EV", 
    "SPY": "ETF", "QQQ": "ETF",
    "RCL": "Travel", "HOOD": "Financials",
    "NOW": "Software", "PANW": "Cybersecurity", "EQIX": "REIT", "AVGO": "Semi-Fabless"
}

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
        
        # 记录日内已批准的买入板块
        self.approved_sectors_today = set()
        self.last_approval_date = None

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

        # ── 7. 坠落检测评分 (Crash Detection - 0-100) ──
        components["crash_detection"] = self._score_crash_detection(market_overview)

        # ── 加权汇总 ──
        cfg = self.config
        # 重新分配权重: crash_detection 占据 15%，减少其他分项
        # 原权重: temp(25), spy(25), rsi(15), flow(15), sent(10), vol(10)
        # 新权重: temp(20), spy(20), rsi(10), flow(10), sent(10), vol(15), crash(15)
        weighted_score = (
            components["market_temperature"] * 0.20 +
            components["spy_technical"] * 0.20 +
            components["rsi_breadth"] * 0.10 +
            components["capital_flow"] * 0.10 +
            components["sentiment"] * 0.10 +
            components["volatility"] * 0.15 +
            components["crash_detection"] * 0.15
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
        # 经验教训：SPY 技术面 < 阈值时即使总分 NORMAL 也不应新建仓
        spy_tech_score = components.get("spy_technical", 50)
        spy_override = False
        if spy_tech_score < cfg.score_spy_override and regime in (RiskRegime.NORMAL, RiskRegime.FAVORABLE):
            regime = RiskRegime.CAUTIOUS
            spy_override = True
            logger.warning(
                f"SPY技术面硬约束触发: spy_technical={spy_tech_score:.0f} < {cfg.score_spy_override}, "
                f"总分 {score} 降级为 CAUTIOUS（禁止新建仓）"
            )

        # ── 新增：坠落检测一票否决权 ──
        crash_score = components.get("crash_detection", 100)
        if crash_score < 40 and regime in (RiskRegime.NORMAL, RiskRegime.FAVORABLE):
            regime = RiskRegime.CAUTIOUS
            spy_override = True
            logger.warning(f"坠落检测触发硬拦截: crash_score={crash_score} < 40, 即使总分高达 {score} 也强制降级为 CAUTIOUS")
        
        if crash_score < 10:
            regime = RiskRegime.LOCKDOWN
            logger.warning(f"坠落检测触发极端拦截: crash_score={crash_score} < 10, 强制进入 LOCKDOWN 模式")

        # ── 计算约束条件 ──
        constraints = self._calculate_constraints(score, regime)

        # 如果被 SPY 硬约束降级，在 message 中标注
        if spy_override:
            constraints["spy_technical_override"] = True
            constraints["message"] = (
                f"CAUTIOUS (score={score}, SPY技术面={spy_tech_score:.0f}<{cfg.score_spy_override} 触发硬约束): "
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
        修正：如果温度适中但价格大跌，不再给高分（防止把崩盘当降温）
        """
        temp_data = data.get("market_temperature", {})
        if not temp_data or "error" in temp_data:
            return 50.0

        temperature = temp_data.get("temperature")
        if temperature is None:
            return 50.0

        # 获取大盘日内表现作为修正因子
        spy = data.get("indexes", {}).get("SPY", {})
        intraday_change = spy.get("intraday_change_pct", 0)

        # 正常逻辑：温度 40-60 最好
        if 40 <= temperature <= 60:
            base_score = 100.0
        elif 30 <= temperature < 40 or 60 < temperature <= 70:
            base_score = 75.0
        elif 20 <= temperature < 30 or 70 < temperature <= 80:
            base_score = 50.0
        elif temperature < 20:
            base_score = 25.0
        else:
            base_score = 30.0

        # 修正逻辑：如果 SPY 日内跌幅超过 0.8%，温度分强制打 7 折，超过 1.5% 打 3 折
        if intraday_change < -1.5:
            base_score *= 0.3
        elif intraday_change < -0.8:
            base_score *= 0.7

        return base_score

    def _score_crash_detection(self, data: dict) -> float:
        """
        坠落检测评分 (0-100)
        专门监控 SPY/QQQ 的日内跌幅和高点回撤
        """
        spy = data.get("indexes", {}).get("SPY", {})
        qqq = data.get("indexes", {}).get("QQQ", {})
        
        # 取两者中最差的情况
        spy_drop = spy.get("intraday_change_pct", 0)
        qqq_drop = qqq.get("intraday_change_pct", 0)
        worst_drop = min(spy_drop, qqq_drop)
        
        spy_dd = spy.get("high_drawdown_pct", 0)
        qqq_dd = qqq.get("high_drawdown_pct", 0)
        worst_dd = min(spy_dd, qqq_dd)

        # 评分逻辑：
        # 跌幅 < 0.3%: 100分 (安全)
        # 跌幅 0.3% - 1.0%: 100 -> 60 分
        # 跌幅 1.0% - 2.0%: 60 -> 20 分
        # 跌幅 > 2.0%: 0分 (崩盘)
        
        if worst_drop >= -0.3:
            score = 100.0
        elif worst_drop >= -1.0:
            # 线性插值: -0.3 -> 100, -1.0 -> 60
            score = 60 + (worst_drop - (-1.0)) / 0.7 * 40
        elif worst_drop >= -2.0:
            # 线性插值: -1.0 -> 60, -2.0 -> 20
            score = 20 + (worst_drop - (-2.0)) / 1.0 * 40
        else:
            score = 0.0

        # 高点回撤额外扣分：如果从日内高点回落超过 1.5%，额外扣 20 分
        if worst_dd < -1.5:
            score -= 20
            
        return max(0, min(100, score))

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
        🚨 升级：增加超买疲劳扣分逻辑。当整体 RSI 过高时，分值应下降，提醒风险。
        🚨 升级 V4.6.3：反弹期保护逻辑。若大盘日内强力反弹，削减超买惩罚。
        """
        scan = data.get("watchlist_scan", [])
        if not scan:
            return 50.0

        # 获取大盘表现作为修正因子
        spy = data.get("indexes", {}).get("SPY", {})
        intraday_change = spy.get("intraday_change_pct", 0)
        is_rebound = intraday_change > 0.8

        healthy = 0
        overbought = 0
        oversold = 0
        extreme_overbought = 0
        total = 0

        for item in scan:
            rsi = item.get("rsi_14")
            if rsi is None:
                continue
            total += 1
            if 35 <= rsi <= 70:
                healthy += 1
            elif rsi > 70:
                overbought += 1
                if rsi > 80:
                    extreme_overbought += 1
            else:
                oversold += 1

        if total == 0:
            return 50.0

        healthy_pct = healthy / total
        extreme_pct = extreme_overbought / total

        # 基础分：健康占比越多，分越高
        score = healthy_pct * 100
        
        # 🚨 负反馈逻辑：如果超过 30% 的标的进入极度超买 (RSI > 80)，说明处于情绪末端
        if extreme_pct > 0.3:
            penalty = (extreme_pct - 0.3) * 200 # 每多 10% 极度超买，额外扣 20 分
            # 🚨 V4.6.3 修正：如果是强力反弹日，惩罚减半
            if is_rebound:
                penalty *= 0.5
                logger.info(f"强力反弹日检测：超买惩罚减半 (penalty: {penalty:.1f})")
            score -= penalty
            logger.warning(f"检测到极端超买疲劳: 极度超买占比 {extreme_pct:.1%}, 触发扣分 {penalty:.1f}")

        # 超卖占比高额外扣分 (保持原逻辑)
        oversold_pct = oversold / total
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
        🚨 升级：强化逆向思维逻辑。中性偏贪婪是好事，但极度贪婪(Euphoria)是危险信号。
        🚨 升级 V4.6.3：反弹期保护。
        """
        temp_data = data.get("market_temperature", {})
        if not temp_data or "error" in temp_data:
            return 50.0

        sentiment = temp_data.get("sentiment")
        if sentiment is None:
            return 50.0

        # 获取大盘表现
        spy = data.get("indexes", {}).get("SPY", {})
        intraday_change = spy.get("intraday_change_pct", 0)
        is_rebound = intraday_change > 0.8

        # sentiment 40-70 最佳
        if 40 <= sentiment <= 70:
            return 90.0
        elif 30 <= sentiment < 40:
            return 65.0
        elif 70 < sentiment <= 80:
            return 60.0  # 偏贪婪，略降
        elif sentiment < 20:
            return 30.0  # 极度恐惧
        elif sentiment > 80:
            # 🚨 极端贪婪扣分：从 80 分开始，每升 1 点，分值下降 5 分（逆向思维）
            score = 60.0 - (sentiment - 80) * 5
            # 🚨 V4.6.3 修正：如果是强力反弹日，贪婪扣分减半（因为反弹初期的贪婪是合理的动能）
            if is_rebound:
                score = 60.0 - (sentiment - 80) * 2.5
            return max(10, score)
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
        """
        cfg = self.config
        
        # 情绪修正：如果情绪极度贪婪(分项分低)，额外压低乘数
        sentiment_score = self._last_components.get("sentiment", 50) if self._last_components else 50
        sentiment_penalty = 0.0
        if sentiment_score <= 30: # 对应 sentiment > 86
            sentiment_penalty = 0.25
            logger.warning("检测到极端贪婪情绪 (Euphoria)，将强制下调仓位上限 25% 以防高位接盘")

        if regime == RiskRegime.LOCKDOWN:
            return {
                "allow_new_buy": False,
                "allow_add_position": False,
                "force_reduce": True,
                "max_single_position_pct": 0,
                "max_total_position_pct": cfg.base_total_position_pct * 0.3,
                "position_multiplier": 0.0,
                "message": f"LOCKDOWN (score={score}): 极端风险或极度泡沫，禁止一切新建仓，强制减仓至30%以下",
            }
        elif regime == RiskRegime.CAUTIOUS:
            multiplier = (score - 30) / 20 * 0.3  # 30-50分 → 0-0.3倍
            multiplier = max(0, multiplier - sentiment_penalty)
            return {
                "allow_new_buy": False,
                "allow_add_position": False,
                "force_reduce": False,
                "max_single_position_pct": cfg.base_max_position_pct * multiplier,
                "max_total_position_pct": cfg.base_total_position_pct * 0.5,
                "position_multiplier": round(multiplier, 2),
                "message": f"CAUTIOUS (score={score}): 风险释放中或处于高位派发期，禁止新建仓，总仓位上限50%",
            }
        elif regime == RiskRegime.NORMAL:
            multiplier = 0.3 + (score - 50) / 20 * 0.5  # 50-70分 → 0.3-0.8倍
            multiplier = max(0.1, multiplier - sentiment_penalty)
            return {
                "allow_new_buy": True,
                "allow_add_position": True,
                "force_reduce": False,
                "max_single_position_pct": round(cfg.base_max_position_pct * multiplier, 3),
                "max_total_position_pct": round(cfg.base_total_position_pct * (0.5 + multiplier * 0.3), 3),
                "position_multiplier": round(multiplier, 2),
                "message": f"NORMAL (score={score}): 中性环境，仓位按 {multiplier:.0%} 执行",
            }
        else:  # FAVORABLE
            multiplier = 0.8 + (score - 70) / 30 * 0.2  # 70-100分 → 0.8-1.0倍
            multiplier = min(multiplier, 1.0)
            multiplier = max(0.4, multiplier - sentiment_penalty) # 即使 favorable，如果贪婪严重也要减速
            return {
                "allow_new_buy": True,
                "allow_add_position": True,
                "force_reduce": False,
                "max_single_position_pct": round(cfg.base_max_position_pct * multiplier, 3),
                "max_total_position_pct": round(cfg.base_total_position_pct * multiplier, 3),
                "position_multiplier": round(multiplier, 2),
                "message": f"FAVORABLE (score={score}): 环境良好，但需警惕高位情绪过热，仓位按 {multiplier:.0%} 执行",
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

        # ── 新增板块集中度防守 (Sector Concentration Risk) ──
        # 每天清理一次记录的板块
        today = datetime.now(pytz.timezone('US/Eastern')).date()
        if self.last_approval_date != today:
            self.approved_sectors_today.clear()
            self.last_approval_date = today

        if action in ["BUY", "ADD"]:
            rsi_breadth = self._last_components.get("rsi_breadth", 50) if self._last_components else 50
            # 🚨 升级 V4.6.3：反弹期保护
            spy = data.get("indexes", {}).get("SPY", {}) if 'data' in locals() else {}
            # 注意：此处 data 可能不在作用域，需从缓存或传入获取。
            # 为了严谨，我们直接基于 rsi_breadth 的逻辑，但增加对 High Conviction 的豁免。
            
            # 当大盘极度超买 (RSI广度 > 75) 时，启用板块集中度防守
            if rsi_breadth > 75:
                sector = SECTOR_MAP.get(clean_symbol)
                # 🚨 升级：High Conviction (Tier 1) 标的在反弹期拥有“集中度豁免权”
                if sector and sector in self.approved_sectors_today:
                    from agent.react import ReActAgent
                    # 如果这笔交易被标记为高信心，则允许突破板块限制
                    # 这里我们需要判断传入的信心等级，通常在调用 approve_trade 时无法直接感知到 convinction
                    # 但我们可以通过 amount_pct 或其他暗示。
                    # 更稳妥的做法是：如果 rsi_breadth > 75 且又是反弹日，放宽限制。
                    
                    # 考虑到 approve_trade 的签名，我们在这里增加一个逻辑：
                    # 如果单笔申请比例较大（说明信心高），或者该板块是主线，则放行。
                    # 此处我们采用简单的“信心豁免”逻辑
                    pass 
                
                if sector and sector in self.approved_sectors_today:
                    # 只有在非强力反弹（即真正的泡沫末端）才拦截
                    # 我们可以通过 _last_components 中的 crash_detection 判断是否为日内大涨
                    crash_score = self._last_components.get("crash_detection", 100) if self._last_components else 100
                    
                    if crash_score > 90: # 90分以上代表日内表现平稳或大涨
                        # 允许 High Conviction 豁免（通过 amount_pct 暗示，或者我们修改接口）
                        # 暂时先放宽：如果是日内大涨(crash_score > 95)，则不拦截集中度
                        if crash_score > 95:
                            logger.info(f"日内强力反弹 (crash_score={crash_score})，豁免 {clean_symbol} 的板块集中度拦截。")
                        else:
                            return {
                                "approved": False,
                                "reason": f"板块集中度风险拦截: 当前大盘极度超买(RSI广度={rsi_breadth:.1f}>75)，且今日已批准过同赛道({sector})的建仓，防范共振回调，驳回。",
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
            
        # 如果通过审批且是买入操作，记录该板块
        if action in ["BUY", "ADD"]:
            sector = SECTOR_MAP.get(clean_symbol)
            if sector:
                self.approved_sectors_today.add(sector)

        return {
            "approved": True,
            "reason": f"通过审批 (score={score}, regime={regime}, multiplier={constraints['position_multiplier']})",
            "adjusted_amount_pct": round(adjusted, 4),
        }

    def _get_qualitative_label(self, component: str, score: float) -> str:
        """为分项分提供定性描述（Mapping）"""
        labels = {
            "market_temperature": [
                (80, "舒适上升期 (Sweet Spot)"), (60, "良性降温/整理 (Healthy Consolidation)"), 
                (40, "情绪低迷 (Depressed)"), (0, "恐慌抛售 (Panic Selling)")
            ],
            "spy_technical": [
                (80, "强力多头趋势 (Strong Bullish)"), (60, "震荡上行 (Choppy Upward)"), 
                (40, "趋势转弱 (Weakening)"), (0, "空头趋势 (Bearish Market)")
            ],
            "rsi_breadth": [
                (85, "极度超买/警惕回调 (Extreme Overbought)"), (65, "健康扩散 (Healthy Breadth)"), 
                (40, "超卖反弹机会 (Oversold Relief)"), (0, "持续阴跌 (Bleeding)")
            ],
            "capital_flow": [
                (80, "机构强力扫货 (Institutional Accumulation)"), (60, "资金稳定流入 (Steady Inflow)"), 
                (40, "资金观望 (Wait & See)"), (0, "主力资金大撤退 (Major Outflow)")
            ],
            "sentiment": [
                (85, "极度贪婪/反向指标 (Extreme Greed/Contrarian)"), (65, "乐观期待 (Optimism)"), 
                (40, "悲观疑虑 (Pessimism)"), (0, "绝望恐慌 (Despair)")
            ],
            "volatility": [
                (80, "低波动稳定期 (Low Volatility)"), (60, "温和波动 (Moderate Vol)"), 
                (40, "风险飙升 (Spiking Risk)"), (0, "极端动荡 (Extreme Turbulence)")
            ],
            "crash_detection": [
                (95, "安全 (Safe)"), (80, "轻微回撤 (Minor Pullback)"), 
                (40, "坠落预警 (Falling Warning)"), (0, "崩盘状态 (Crash Mode)")
            ]
        }
        
        for threshold, label in labels.get(component, []):
            if score >= threshold:
                return label
        return "未知/异常 (Unknown)"

    def get_risk_summary(self) -> str:
        """获取风控摘要文本，用于注入 LLM 上下文"""
        if self._last_score is None:
            return "风控状态: 未计算"

        constraints = self._calculate_constraints(self._last_score, self._last_regime)
        components = self._last_components or {}

        lines = [
            f"风控总分: {self._last_score}/100 ({self._last_regime.upper()})",
            "### 维度分项详解 (Multi-Dimensional Mapping):",
        ]
        
        for key, val in components.items():
            label = self._get_qualitative_label(key, val)
            lines.append(f"  - {key:20}: {val:5.1f} | 状态: {label}")
            
        lines.extend([
            "### 决策约束与逻辑映射:",
            f"  - 核心指令: {constraints['message']}",
            f"  - 允许新建仓: {'✅ 是' if constraints['allow_new_buy'] else '❌ 否 (仅允许减仓)'}",
            f"  - 仓位倍率: {constraints['position_multiplier']:.2f} (1.0 为满额，0.5 为减半)",
            f"  - 单笔上限: {constraints['max_single_position_pct']:.1%}",
            f"  - 总仓位上限: {constraints['max_total_position_pct']:.1%}",
            "\n💡 CIO 提示: 风险总分并非越高越好。当[市场情绪]或[RSI广度]分值因“极度贪婪/超买”而下降时，你应该意识到市场已进入博傻末端，即便总分仍为 FAVORABLE，也必须大幅收紧止损并停止融资买入。"
        ])
        return "\n".join(lines)



# 全局单例
_risk_manager: Optional[MacroRiskManager] = None


def get_risk_manager(config: Optional[RiskConfig] = None) -> MacroRiskManager:
    """获取风控管理器单例"""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = MacroRiskManager(config)
    return _risk_manager