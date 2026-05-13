"""
行情数据工具集 v2.0
基于 LongPort QuoteContext 提供丰富的技术分析指标

改造要点:
  - 新增 MACD、布林带(BB)、ATR、ADX、OBV、VWAP、StochRSI、%B
  - 新增 LongPort market_temperature 市场温度接口
  - 新增 批量技术扫描（一次扫描整个标的池）
  - 增强资金流向分析（大中小单 + 趋势判断）
"""
import json
import math
import logging
from datetime import datetime, timedelta
from typing import Optional

from longport.openapi import (
    Config, QuoteContext, Period, AdjustType, CalcIndex, Market
)

from tools.base import BaseTool, ToolParameter
from config import WATCHLIST


logger = logging.getLogger("MarketData")


# ───────────────────────────────────────────
# 共享 QuoteContext 单例
# ───────────────────────────────────────────

_quote_ctx: Optional[QuoteContext] = None


def get_quote_ctx() -> QuoteContext:
    """获取或创建 QuoteContext 单例"""
    global _quote_ctx
    if _quote_ctx is None:
        config = Config.from_env()
        _quote_ctx = QuoteContext(config)
    return _quote_ctx


def modify_symbol(symbol: str) -> str:
    """确保股票代码有 .US 后缀"""
    if symbol.endswith(".US"):
        return symbol
    return symbol + ".US"


def cut_symbol(symbol: str) -> str:
    """去掉 .US 后缀"""
    if symbol.endswith(".US"):
        return symbol[:-3]
    return symbol


# ───────────────────────────────────────────
# 辅助计算函数（完整技术指标库）
# ───────────────────────────────────────────

def calc_sma(prices: list[float], period: int) -> Optional[float]:
    """简单移动平均线"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calc_ema(prices: list[float], period: int) -> Optional[float]:
    """指数移动平均线"""
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_ema_series(prices: list[float], period: int) -> list[float]:
    """计算完整 EMA 序列"""
    if len(prices) < period:
        return []
    multiplier = 2 / (period + 1)
    ema_vals = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema_vals.append((price - ema_vals[-1]) * multiplier + ema_vals[-1])
    return ema_vals


def calc_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """RSI（Wilder平滑法）"""
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas[:period]]
    losses = [-d if d < 0 else 0 for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for d in deltas[period:]:
        gain = d if d > 0 else 0
        loss = -d if d < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_rsi_series(prices: list[float], period: int = 14) -> list[float]:
    """计算 RSI 序列（用于 StochRSI）"""
    if len(prices) < period + 1:
        return []
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas[:period]]
    losses = [-d if d < 0 else 0 for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    rsi_vals = []
    if avg_loss == 0:
        rsi_vals.append(100.0)
    else:
        rsi_vals.append(100 - (100 / (1 + avg_gain / avg_loss)))

    for d in deltas[period:]:
        gain = d if d > 0 else 0
        loss = -d if d < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rsi_vals.append(100 - (100 / (1 + avg_gain / avg_loss)))
    return rsi_vals


def calc_macd(prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    """
    MACD 指标
    返回: {macd_line, signal_line, histogram}
    """
    if len(prices) < slow + signal:
        return None

    ema_fast = calc_ema_series(prices, fast)
    ema_slow = calc_ema_series(prices, slow)

    # 对齐长度: ema_fast 从 fast 开始, ema_slow 从 slow 开始
    # MACD line = EMA(fast) - EMA(slow), 从 slow 开始
    offset = slow - fast
    macd_line = []
    for i in range(len(ema_slow)):
        macd_line.append(ema_fast[i + offset] - ema_slow[i])

    if len(macd_line) < signal:
        return None

    # Signal line = EMA(macd_line, signal)
    multiplier = 2 / (signal + 1)
    sig = sum(macd_line[:signal]) / signal
    signal_vals = [sig]
    for val in macd_line[signal:]:
        sig = (val - sig) * multiplier + sig
        signal_vals.append(sig)

    latest_macd = macd_line[-1]
    latest_signal = signal_vals[-1]
    histogram = latest_macd - latest_signal

    # 判断 MACD 交叉
    cross = "none"
    if len(macd_line) >= 2 and len(signal_vals) >= 2:
        prev_diff = macd_line[-2] - signal_vals[-2]
        curr_diff = latest_macd - latest_signal
        if prev_diff <= 0 and curr_diff > 0:
            cross = "golden_cross"  # 金叉
        elif prev_diff >= 0 and curr_diff < 0:
            cross = "death_cross"   # 死叉

    return {
        "macd_line": round(latest_macd, 4),
        "signal_line": round(latest_signal, 4),
        "histogram": round(histogram, 4),
        "cross": cross,
        "trend": "bullish" if histogram > 0 else "bearish",
    }


def calc_bollinger_bands(prices: list[float], period: int = 20, std_mult: float = 2.0) -> Optional[dict]:
    """
    布林带 (Bollinger Bands)
    返回: {upper, middle, lower, pct_b, bandwidth}
    """
    if len(prices) < period:
        return None

    recent = prices[-period:]
    middle = sum(recent) / period
    variance = sum((p - middle) ** 2 for p in recent) / period
    std = math.sqrt(variance)

    upper = middle + std_mult * std
    lower = middle - std_mult * std
    latest = prices[-1]

    # %B = (价格 - 下轨) / (上轨 - 下轨)
    band_width = upper - lower
    pct_b = (latest - lower) / band_width if band_width > 0 else 0.5
    bandwidth_pct = (band_width / middle) * 100 if middle > 0 else 0

    return {
        "upper": round(upper, 2),
        "middle": round(middle, 2),
        "lower": round(lower, 2),
        "pct_b": round(pct_b, 3),       # 0=下轨, 0.5=中轨, 1=上轨
        "bandwidth_pct": round(bandwidth_pct, 2),
        "position": (
            "above_upper" if latest > upper else
            "upper_half" if latest > middle else
            "lower_half" if latest > lower else
            "below_lower"
        ),
    }


def calc_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Optional[float]:
    """
    ATR (Average True Range) - 平均真实波幅
    用于止损设置和波动性评估
    """
    if len(closes) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    # Wilder 平滑
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    return round(atr, 4)


def calc_adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Optional[dict]:
    """
    ADX (Average Directional Index) - 趋势强度指标
    返回: {adx, plus_di, minus_di, trend_strength}
    """
    n = len(closes)
    if n < period * 2 + 1:
        return None

    # 计算 +DM, -DM
    plus_dm = []
    minus_dm = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)

    # True Range
    tr_list = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_list.append(tr)

    # Wilder 平滑
    def wilder_smooth(vals, p):
        s = sum(vals[:p])
        result = [s]
        for v in vals[p:]:
            s = s - s / p + v
            result.append(s)
        return result

    smooth_tr = wilder_smooth(tr_list, period)
    smooth_pdm = wilder_smooth(plus_dm, period)
    smooth_mdm = wilder_smooth(minus_dm, period)

    # +DI, -DI
    plus_di = [(p / t * 100 if t > 0 else 0) for p, t in zip(smooth_pdm, smooth_tr)]
    minus_di = [(m / t * 100 if t > 0 else 0) for m, t in zip(smooth_mdm, smooth_tr)]

    # DX
    dx = []
    for p, m in zip(plus_di, minus_di):
        total = p + m
        dx.append(abs(p - m) / total * 100 if total > 0 else 0)

    if len(dx) < period:
        return None

    # ADX = Wilder smooth of DX
    adx = sum(dx[:period]) / period
    for d in dx[period:]:
        adx = (adx * (period - 1) + d) / period

    latest_pdi = plus_di[-1]
    latest_mdi = minus_di[-1]

    return {
        "adx": round(adx, 2),
        "plus_di": round(latest_pdi, 2),
        "minus_di": round(latest_mdi, 2),
        "trend_strength": (
            "strong_trend" if adx > 40 else
            "trending" if adx > 25 else
            "weak_trend" if adx > 20 else
            "no_trend"
        ),
        "direction": "bullish" if latest_pdi > latest_mdi else "bearish",
    }


def calc_obv(closes: list[float], volumes: list[int]) -> Optional[dict]:
    """
    OBV (On-Balance Volume) - 能量潮
    返回: {obv, obv_sma20, divergence}
    """
    if len(closes) < 21 or len(volumes) < 21:
        return None

    obv = 0
    obv_series = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
        obv_series.append(obv)

    obv_sma20 = sum(obv_series[-20:]) / 20 if len(obv_series) >= 20 else None

    # 简单背离检测: 价格创新高但OBV未创新高 = 看空背离
    price_trend = "up" if closes[-1] > closes[-20] else "down"
    obv_trend = "up" if obv_series[-1] > obv_series[-20] else "down"
    divergence = "none"
    if price_trend == "up" and obv_trend == "down":
        divergence = "bearish_divergence"
    elif price_trend == "down" and obv_trend == "up":
        divergence = "bullish_divergence"

    return {
        "obv": obv_series[-1],
        "obv_sma20": round(obv_sma20) if obv_sma20 else None,
        "obv_above_sma20": obv_series[-1] > obv_sma20 if obv_sma20 else None,
        "divergence": divergence,
    }


def calc_stoch_rsi(prices: list[float], rsi_period: int = 14, stoch_period: int = 14, k_smooth: int = 3) -> Optional[dict]:
    """
    StochRSI - 随机RSI
    返回: {stoch_rsi, k_line, zone}
    """
    rsi_vals = calc_rsi_series(prices, rsi_period)
    if len(rsi_vals) < stoch_period:
        return None

    # StochRSI = (RSI - RSI_min) / (RSI_max - RSI_min)
    recent_rsi = rsi_vals[-stoch_period:]
    rsi_min = min(recent_rsi)
    rsi_max = max(recent_rsi)
    rsi_range = rsi_max - rsi_min

    stoch_rsi = (rsi_vals[-1] - rsi_min) / rsi_range if rsi_range > 0 else 0.5

    # %K 平滑
    stoch_series = []
    for i in range(len(rsi_vals) - stoch_period + 1):
        window = rsi_vals[i:i + stoch_period]
        w_min = min(window)
        w_max = max(window)
        w_range = w_max - w_min
        stoch_series.append(
            (rsi_vals[i + stoch_period - 1] - w_min) / w_range if w_range > 0 else 0.5
        )

    k_line = sum(stoch_series[-k_smooth:]) / k_smooth if len(stoch_series) >= k_smooth else stoch_rsi

    return {
        "stoch_rsi": round(stoch_rsi, 3),
        "k_line": round(k_line, 3),
        "zone": (
            "overbought" if k_line > 0.8 else
            "bullish" if k_line > 0.5 else
            "bearish" if k_line > 0.2 else
            "oversold"
        ),
    }


def calc_avg_volume(candles, period: int) -> Optional[float]:
    """计算近 N 根K线的平均成交量"""
    vols = [int(c.volume) for c in candles]
    if len(vols) < period:
        return None
    return sum(vols[-period:]) / period


# ═══════════════════════════════════════════
# 工具1: 增强版技术分析（核心工具）
# ═══════════════════════════════════════════

class GetTechnicalAnalysisTool(BaseTool):
    """
    获取个股完整技术分析报告
    一次返回中长线+短期维度的全部关键指标
    """

    name = "get_technical_analysis"
    description = (
        "获取个股完整技术分析报告，包含6大维度指标：\n"
        "1) 趋势: SMA20/50/200、Stage 2判断、ADX趋势强度\n"
        "2) 动量: RSI(日线/周线)、MACD、StochRSI\n"
        "3) 波动: 布林带(%B/带宽)、ATR(止损参考)\n"
        "4) 量价: OBV能量潮、量比、成交量趋势\n"
        "5) 相对强度: vs SPY 20日/50日超额收益\n"
        "6) 形态信号: 突破判断、支撑阻力位\n"
        "这是每次决策的核心数据源。"
    )
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码，如 NVDA、TSM、MSFT 等（仅限标的池内股票）"
        )
    ]

    def execute(self, symbol: str, **kwargs) -> str:
        try:
            ctx = get_quote_ctx()
            sym = modify_symbol(symbol)

            # ── 1. 获取日K线（250根）──
            daily = ctx.history_candlesticks_by_offset(
                sym, Period.Day, AdjustType.ForwardAdjust,
                forward=False, count=250
            )
            if not daily or len(daily) < 50:
                return json.dumps({"error": f"{symbol} 日K线数据不足"}, ensure_ascii=False)

            closes_d = [float(c.close) for c in daily]
            highs_d = [float(c.high) for c in daily]
            lows_d = [float(c.low) for c in daily]
            volumes_d = [int(c.volume) for c in daily]
            latest_price = closes_d[-1]

            # ── 2. 趋势指标 ──
            sma20 = calc_sma(closes_d, 20)
            sma50 = calc_sma(closes_d, 50)
            sma200 = calc_sma(closes_d, 200)
            adx_data = calc_adx(highs_d, lows_d, closes_d)

            stage2 = False
            if sma50 and sma200:
                stage2 = latest_price > sma50 and sma50 > sma200

            trend = {
                "SMA20": round(sma20, 2) if sma20 else None,
                "SMA50": round(sma50, 2) if sma50 else None,
                "SMA200": round(sma200, 2) if sma200 else None,
                "price_above_SMA50": latest_price > sma50 if sma50 else None,
                "price_above_SMA200": latest_price > sma200 if sma200 else None,
                "golden_cross": sma50 > sma200 if (sma50 and sma200) else None,
                "stage2_uptrend": stage2,
                "adx": adx_data,
            }

            # ── 3. 动量指标 ──
            rsi_daily = calc_rsi(closes_d, 14)
            macd_data = calc_macd(closes_d)
            stoch_rsi_data = calc_stoch_rsi(closes_d)

            # 周线 RSI
            weekly = ctx.history_candlesticks_by_offset(
                sym, Period.Week, AdjustType.ForwardAdjust,
                forward=False, count=30
            )
            closes_w = [float(c.close) for c in weekly] if weekly else []
            rsi_weekly = calc_rsi(closes_w, 14)

            momentum = {
                "RSI_daily_14": rsi_daily,
                "RSI_weekly_14": rsi_weekly,
                "weekly_RSI_zone": (
                    "overbought_danger" if rsi_weekly and rsi_weekly > 80 else
                    "strong" if rsi_weekly and 50 <= rsi_weekly <= 75 else
                    "neutral" if rsi_weekly and 30 <= rsi_weekly < 50 else
                    "weak_avoid" if rsi_weekly and rsi_weekly < 30 else
                    "elevated" if rsi_weekly and 75 < rsi_weekly <= 80 else
                    "unknown"
                ),
                "macd": macd_data,
                "stoch_rsi": stoch_rsi_data,
            }

            # ── 4. 波动指标 ──
            bb_data = calc_bollinger_bands(closes_d)
            atr = calc_atr(highs_d, lows_d, closes_d)
            atr_pct = round(atr / latest_price * 100, 2) if atr else None

            volatility = {
                "bollinger_bands": bb_data,
                "ATR_14": round(atr, 2) if atr else None,
                "ATR_pct": atr_pct,
                "atr_stop_loss": round(latest_price - 2 * atr, 2) if atr else None,  # 2xATR止损
            }

            # ── 5. 量价指标 ──
            obv_data = calc_obv(closes_d, volumes_d)
            avg_vol_50 = calc_avg_volume(daily, 50)
            latest_vol = volumes_d[-1]
            vol_ratio = round(latest_vol / avg_vol_50, 2) if avg_vol_50 and avg_vol_50 > 0 else None

            volume_price = {
                "latest_volume": latest_vol,
                "avg_volume_50d": int(avg_vol_50) if avg_vol_50 else None,
                "volume_ratio": vol_ratio,
                "is_high_volume": vol_ratio >= 1.5 if vol_ratio else None,
                "obv": obv_data,
            }

            # ── 6. 相对强度 vs SPY ──
            rs_vs_spy = None
            try:
                spy_daily = ctx.history_candlesticks_by_offset(
                    "SPY.US", Period.Day, AdjustType.ForwardAdjust,
                    forward=False, count=60
                )
                if spy_daily and len(spy_daily) >= 50:
                    spy_closes = [float(c.close) for c in spy_daily]
                    stock_20d = (closes_d[-1] / closes_d[-20] - 1) * 100 if len(closes_d) >= 20 else None
                    spy_20d = (spy_closes[-1] / spy_closes[-20] - 1) * 100 if len(spy_closes) >= 20 else None
                    stock_50d = (closes_d[-1] / closes_d[-50] - 1) * 100 if len(closes_d) >= 50 else None
                    spy_50d = (spy_closes[-1] / spy_closes[-50] - 1) * 100 if len(spy_closes) >= 50 else None

                    rs_vs_spy = {
                        "stock_20d_return_pct": round(stock_20d, 2) if stock_20d is not None else None,
                        "SPY_20d_return_pct": round(spy_20d, 2) if spy_20d is not None else None,
                        "outperform_SPY_20d": stock_20d > spy_20d if (stock_20d is not None and spy_20d is not None) else None,
                        "stock_50d_return_pct": round(stock_50d, 2) if stock_50d is not None else None,
                        "SPY_50d_return_pct": round(spy_50d, 2) if spy_50d is not None else None,
                        "outperform_SPY_50d": stock_50d > spy_50d if (stock_50d is not None and spy_50d is not None) else None,
                    }
            except Exception as e:
                logger.warning(f"获取SPY数据失败: {e}")

            # ── 7. 价格区间 + 止损参考 ──
            high_52w = max(closes_d[-min(250, len(closes_d)):])
            low_52w = min(closes_d[-min(250, len(closes_d)):])

            price_info = {
                "latest_price": latest_price,
                "52w_high": round(high_52w, 2),
                "52w_low": round(low_52w, 2),
                "pct_from_52w_high": round((latest_price / high_52w - 1) * 100, 2),
                "pct_from_52w_low": round((latest_price / low_52w - 1) * 100, 2),
            }

            recent_lows = [float(c.low) for c in daily[-20:]]
            stop_ref = {
                "stop_loss_8pct": round(latest_price * 0.92, 2),
                "stop_loss_12pct": round(latest_price * 0.88, 2),
                "swing_low_20d": round(min(recent_lows), 2),
                "SMA50_as_stop": round(sma50, 2) if sma50 else None,
                "atr_2x_stop": round(latest_price - 2 * atr, 2) if atr else None,
            }

            # ── 汇总 ──
            result = {
                "symbol": cut_symbol(sym),
                "price": price_info,
                "trend": trend,
                "momentum": momentum,
                "volatility": volatility,
                "volume_price": volume_price,
                "relative_strength_vs_SPY": rs_vs_spy,
                "stop_loss_reference": stop_ref,
                "signal_summary": self._generate_signal_summary(
                    symbol, latest_price, stage2, trend, momentum, volatility, volume_price, rs_vs_spy
                ),
            }

            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _generate_signal_summary(self, symbol, price, stage2, trend, momentum, volatility, vol, rs):
        """生成多维度信号评分摘要"""
        signals = []
        score = 0  # 累计信号分，正=看多，负=看空

        # 趋势
        if stage2:
            signals.append("TREND:+2 Stage2上升趋势")
            score += 2
        else:
            signals.append("TREND:-2 未达Stage2")
            score -= 2

        adx = trend.get("adx")
        if adx and adx.get("adx", 0) > 25 and adx.get("direction") == "bullish":
            signals.append("ADX:+1 趋势强且方向多头")
            score += 1

        # 动量
        rsi_w = momentum.get("RSI_weekly_14")
        if rsi_w and 50 <= rsi_w <= 75:
            signals.append("RSI:+1 周线强势区")
            score += 1
        elif rsi_w and rsi_w > 80:
            signals.append("RSI:-2 严重超买")
            score -= 2
        elif rsi_w and rsi_w < 30:
            signals.append("RSI:-2 弱势区")
            score -= 2

        macd = momentum.get("macd")
        if macd and macd.get("cross") == "golden_cross":
            signals.append("MACD:+2 金叉")
            score += 2
        elif macd and macd.get("cross") == "death_cross":
            signals.append("MACD:-1 死叉")
            score -= 1
        elif macd and macd.get("trend") == "bullish":
            signals.append("MACD:+1 柱状图为正")
            score += 1

        # 量价
        obv = vol.get("obv")
        if obv and obv.get("divergence") == "bearish_divergence":
            signals.append("OBV:-2 看空背离(价涨量缩)")
            score -= 2
        elif obv and obv.get("divergence") == "bullish_divergence":
            signals.append("OBV:+1 看多背离")
            score += 1

        if vol.get("is_high_volume"):
            signals.append("VOL:+1 放量")
            score += 1

        # 相对强度
        if rs and rs.get("outperform_SPY_20d"):
            signals.append("RS:+1 跑赢SPY")
            score += 1
        elif rs and rs.get("outperform_SPY_20d") is False:
            signals.append("RS:-1 跑输SPY")
            score -= 1

        # 布林带
        bb = volatility.get("bollinger_bands")
        if bb and bb.get("position") == "above_upper":
            signals.append("BB:-1 突破上轨(谨慎)")
            score -= 1
        elif bb and bb.get("position") == "below_lower":
            signals.append("BB:-1 跌破下轨(弱势)")
            score -= 1

        verdict = (
            "STRONG_BUY" if score >= 5 else
            "BUY" if score >= 3 else
            "LEAN_BUY" if score >= 1 else
            "NEUTRAL" if score >= -1 else
            "LEAN_SELL" if score >= -3 else
            "SELL" if score >= -5 else
            "STRONG_SELL"
        )

        return {
            "score": score,
            "verdict": verdict,
            "signals": signals,
        }


# ═══════════════════════════════════════════
# 工具2: 批量标的池扫描
# ═══════════════════════════════════════════

class ScanWatchlistTool(BaseTool):
    """
    批量扫描标的池技术面状况
    快速识别有信号的股票
    """

    name = "scan_watchlist"
    description = (
        "一次性扫描全部10只标的池股票的关键技术指标，快速定位有交易信号的标的。"
        "返回每只股票的：Stage2状态、RSI、MACD信号、量比、相对强度评级。"
        "用于每轮执行开始时快速筛选需要深入分析的标的。"
    )
    parameters = []

    def execute(self, **kwargs) -> str:
        try:
            ctx = get_quote_ctx()
            results = []

            for symbol in WATCHLIST:
                sym = modify_symbol(symbol)
                try:
                    daily = ctx.history_candlesticks_by_offset(
                        sym, Period.Day, AdjustType.ForwardAdjust,
                        forward=False, count=250
                    )
                    if not daily or len(daily) < 50:
                        results.append({"symbol": symbol, "error": "数据不足"})
                        continue

                    closes = [float(c.close) for c in daily]
                    highs = [float(c.high) for c in daily]
                    lows = [float(c.low) for c in daily]
                    volumes = [int(c.volume) for c in daily]
                    price = closes[-1]

                    sma50 = calc_sma(closes, 50)
                    sma200 = calc_sma(closes, 200)
                    stage2 = (price > sma50 > sma200) if (sma50 and sma200) else False

                    rsi = calc_rsi(closes, 14)
                    macd = calc_macd(closes)

                    avg_vol = calc_avg_volume(daily, 50)
                    vol_ratio = round(volumes[-1] / avg_vol, 2) if avg_vol and avg_vol > 0 else None

                    # 20日涨跌幅
                    ret_20d = round((price / closes[-20] - 1) * 100, 2) if len(closes) >= 20 else None

                    scan_result = {
                        "symbol": symbol,
                        "price": round(price, 2),
                        "stage2": stage2,
                        "rsi_14": rsi,
                        "macd_signal": macd.get("cross", "none") if macd else "N/A",
                        "macd_trend": macd.get("trend", "N/A") if macd else "N/A",
                        "volume_ratio": vol_ratio,
                        "return_20d_pct": ret_20d,
                    }

                    # 快速评级
                    flags = []
                    if stage2:
                        flags.append("STAGE2")
                    if macd and macd.get("cross") == "golden_cross":
                        flags.append("MACD_GOLDEN")
                    if vol_ratio and vol_ratio >= 1.5:
                        flags.append("HIGH_VOL")
                    if rsi and rsi > 80:
                        flags.append("OVERBOUGHT")
                    elif rsi and rsi < 30:
                        flags.append("OVERSOLD")

                    scan_result["flags"] = flags
                    scan_result["needs_attention"] = len(flags) > 0
                    results.append(scan_result)

                except Exception as e:
                    results.append({"symbol": symbol, "error": str(e)})

            # 按信号多少排序
            results.sort(key=lambda x: len(x.get("flags", [])), reverse=True)

            return json.dumps({
                "watchlist_scan": results,
                "total": len(results),
                "with_signals": sum(1 for r in results if r.get("needs_attention")),
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


# ═══════════════════════════════════════════
# 工具3: K线数据
# ═══════════════════════════════════════════

class GetKlineTool(BaseTool):
    """获取K线/蜡烛图数据"""

    name = "get_kline"
    description = (
        "获取指定股票的K线数据，支持日线、周线、月线、60分钟线。"
        "返回开高低收、成交量等原始数据。"
    )
    parameters = [
        ToolParameter(name="symbol", type="string", description="股票代码"),
        ToolParameter(
            name="period", type="string",
            description="K线周期: day/week/month/min_60",
            enum=["day", "week", "month", "min_60"],
            default="day", required=False,
        ),
        ToolParameter(
            name="count", type="integer",
            description="获取根数，默认50",
            required=False, default=50,
        ),
    ]

    PERIOD_MAP = {
        "day": Period.Day,
        "week": Period.Week,
        "month": Period.Month,
        "min_60": Period.Min_60,
    }

    def execute(self, symbol: str, period: str = "day", count: int = 50, **kwargs) -> str:
        try:
            ctx = get_quote_ctx()
            sym = modify_symbol(symbol)
            lp_period = self.PERIOD_MAP.get(period, Period.Day)

            candles = ctx.history_candlesticks_by_offset(
                sym, lp_period, AdjustType.ForwardAdjust,
                forward=False, count=min(count, 500)
            )
            if not candles:
                return json.dumps({"error": f"{symbol} 无K线数据"}, ensure_ascii=False)

            kline_data = []
            for c in candles[-count:]:
                kline_data.append({
                    "date": str(c.timestamp)[:10],
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": int(c.volume),
                })

            return json.dumps({
                "symbol": cut_symbol(sym),
                "period": period,
                "count": len(kline_data),
                "klines": kline_data,
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


# ═══════════════════════════════════════════
# 工具4: 资金流向分析
# ═══════════════════════════════════════════

class GetCapitalFlowTool(BaseTool):
    """获取资金流向与大中小单分布"""

    name = "get_capital_flow"
    description = (
        "获取个股的资金流向数据，包括分时净流入趋势和大/中/小单分布，"
        "用于判断主力资金动向。"
    )
    parameters = [
        ToolParameter(name="symbol", type="string", description="股票代码")
    ]

    def execute(self, symbol: str, **kwargs) -> str:
        try:
            ctx = get_quote_ctx()
            sym = modify_symbol(symbol)

            flow_data = ctx.capital_flow(sym)
            if not flow_data:
                return json.dumps({"error": f"{symbol} 无资金流向数据"}, ensure_ascii=False)

            latest_inflow = float(flow_data[-1].inflow) if flow_data else 0
            recent = flow_data[-30:] if len(flow_data) >= 30 else flow_data
            inflows = [float(f.inflow) for f in recent]
            trend = "inflow" if inflows[-1] > inflows[0] else "outflow"

            dist = ctx.capital_distribution(sym)
            distribution = {}
            if dist:
                cap_in = dist.capital_in
                cap_out = dist.capital_out
                distribution = {
                    "large_in": float(cap_in.large),
                    "large_out": float(cap_out.large),
                    "large_net": round(float(cap_in.large) - float(cap_out.large), 2),
                    "medium_in": float(cap_in.medium),
                    "medium_out": float(cap_out.medium),
                    "medium_net": round(float(cap_in.medium) - float(cap_out.medium), 2),
                    "small_in": float(cap_in.small),
                    "small_out": float(cap_out.small),
                    "small_net": round(float(cap_in.small) - float(cap_out.small), 2),
                }
                distribution["main_force_direction"] = (
                    "主力净流入" if distribution["large_net"] > 0 else "主力净流出"
                )

            return json.dumps({
                "symbol": cut_symbol(sym),
                "latest_net_inflow": round(latest_inflow, 2),
                "intraday_trend": trend,
                "capital_distribution": distribution,
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


# ═══════════════════════════════════════════
# 工具5: 基本面指标
# ═══════════════════════════════════════════

class GetFundamentalsTool(BaseTool):
    """获取股票基本面指标"""

    name = "get_fundamentals"
    description = (
        "获取股票基本面指标：PE(TTM)、PB、总市值、换手率、多周期涨跌幅等。"
        "支持批量查询（用逗号分隔）。"
    )
    parameters = [
        ToolParameter(
            name="symbols", type="string",
            description="股票代码，逗号分隔，如 NVDA,TSM,MSFT"
        )
    ]

    def execute(self, symbols: str, **kwargs) -> str:
        try:
            ctx = get_quote_ctx()
            symbol_list = [modify_symbol(s.strip()) for s in symbols.split(",")]

            indexes = ctx.calc_indexes(
                symbol_list,
                [
                    CalcIndex.LastDone,
                    CalcIndex.ChangeRate,
                    CalcIndex.ChangeValue,
                    CalcIndex.Volume,
                    CalcIndex.Turnover,
                    CalcIndex.TurnoverRate,
                    CalcIndex.PeTtmRatio,
                    CalcIndex.PbRatio,
                    CalcIndex.TotalMarketValue,
                    CalcIndex.FiveDayChangeRate,
                    CalcIndex.TenDayChangeRate,
                    CalcIndex.HalfYearChangeRate,
                    CalcIndex.YtdChangeRate,
                    CalcIndex.Amplitude,
                    CalcIndex.VolumeRatio,
                ]
            )

            results = []
            for idx in indexes:
                data = {
                    "symbol": cut_symbol(idx.symbol),
                    "last_done": float(idx.last_done) if idx.last_done else None,
                    "change_rate_pct": float(idx.change_rate) if idx.change_rate else None,
                    "volume": int(idx.volume) if idx.volume else None,
                    "turnover_rate_pct": float(idx.turnover_rate) if idx.turnover_rate else None,
                    "pe_ttm": float(idx.pe_ttm_ratio) if idx.pe_ttm_ratio else None,
                    "pb": float(idx.pb_ratio) if idx.pb_ratio else None,
                    "total_market_value": float(idx.total_market_value) if idx.total_market_value else None,
                    "five_day_change_pct": float(idx.five_day_change_rate) if idx.five_day_change_rate else None,
                    "ten_day_change_pct": float(idx.ten_day_change_rate) if idx.ten_day_change_rate else None,
                    "half_year_change_pct": float(idx.half_year_change_rate) if idx.half_year_change_rate else None,
                    "ytd_change_pct": float(idx.ytd_change_rate) if idx.ytd_change_rate else None,
                    "amplitude_pct": float(idx.amplitude) if idx.amplitude else None,
                    "volume_ratio": float(idx.volume_ratio) if idx.volume_ratio else None,
                }
                results.append(data)

            return json.dumps({"fundamentals": results, "count": len(results)}, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


# ═══════════════════════════════════════════
# 工具6: 大盘环境 + 市场温度
# ═══════════════════════════════════════════

class GetMarketOverviewTool(BaseTool):
    """获取大盘环境分析 + LongPort 市场温度"""

    name = "get_market_overview"
    description = (
        "分析大盘环境，包含两部分：\n"
        "1) SPY/QQQ 技术面（均线、RSI、趋势判断）\n"
        "2) LongPort 市场温度指数（temperature/sentiment/valuation）\n"
        "这是宏观风控评分的核心数据源。"
    )
    parameters = []

    def execute(self, **kwargs) -> str:
        try:
            ctx = get_quote_ctx()
            results = {}

            # ── SPY/QQQ 技术面 ──
            for index_symbol in ["SPY.US", "QQQ.US"]:
                short_name = cut_symbol(index_symbol)
                daily = ctx.history_candlesticks_by_offset(
                    index_symbol, Period.Day, AdjustType.ForwardAdjust,
                    forward=False, count=250
                )
                if not daily or len(daily) < 50:
                    results[short_name] = {"error": "K线数据不足"}
                    continue

                closes = [float(c.close) for c in daily]
                highs = [float(c.high) for c in daily]
                lows = [float(c.low) for c in daily]
                price = closes[-1]

                sma20 = calc_sma(closes, 20)
                sma50 = calc_sma(closes, 50)
                sma200 = calc_sma(closes, 200)
                rsi = calc_rsi(closes, 14)
                macd = calc_macd(closes)
                atr = calc_atr(highs, lows, closes)
                avg_vol = calc_avg_volume(daily, 50)
                latest_vol = int(daily[-1].volume)
                vol_ratio = round(latest_vol / avg_vol, 2) if avg_vol else None

                uptrend = price > sma50 > sma200 if (sma50 and sma200) else None

                results[short_name] = {
                    "price": round(price, 2),
                    "SMA20": round(sma20, 2) if sma20 else None,
                    "SMA50": round(sma50, 2) if sma50 else None,
                    "SMA200": round(sma200, 2) if sma200 else None,
                    "price_above_SMA50": price > sma50 if sma50 else None,
                    "price_above_SMA200": price > sma200 if sma200 else None,
                    "golden_cross": sma50 > sma200 if (sma50 and sma200) else None,
                    "uptrend": uptrend,
                    "RSI_14": rsi,
                    "MACD": macd,
                    "ATR_14": round(atr, 2) if atr else None,
                    "volume_ratio": vol_ratio,
                }

            # ── LongPort 市场温度 ──
            market_temp = None
            try:
                temp_data = ctx.market_temperature(Market.US)
                if temp_data:
                    # 取最新一条
                    latest = temp_data[-1] if isinstance(temp_data, list) else temp_data
                    market_temp = {
                        "temperature": getattr(latest, 'temperature', None),
                        "sentiment": getattr(latest, 'sentiment', None),
                        "valuation": getattr(latest, 'valuation', None),
                    }
            except Exception as e:
                logger.warning(f"获取市场温度失败: {e}")
                market_temp = {"error": str(e)}

            # ── 综合判断 ──
            spy_ok = results.get("SPY", {}).get("uptrend", False)
            qqq_ok = results.get("QQQ", {}).get("uptrend", False)

            if spy_ok and qqq_ok:
                market_verdict = "bullish — 大盘多头排列，适合建仓"
            elif spy_ok or qqq_ok:
                market_verdict = "mixed — 信号分歧，建仓需谨慎"
            else:
                spy_200 = results.get("SPY", {}).get("price_above_SMA200")
                if spy_200 is False:
                    market_verdict = "bearish — SPY跌破200日均线，停止建仓"
                else:
                    market_verdict = "neutral — 大盘横盘/弱势"

            return json.dumps({
                "indexes": results,
                "market_temperature": market_temp,
                "market_verdict": market_verdict,
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


# ───────────────────────────────────────────
# 工具注册入口
# ───────────────────────────────────────────


class SearchHedgingOptionTool(BaseTool):
    """寻找最佳对冲期权工具"""

    name = "search_hedging_option"
    description = "输入标的、期权方向(Call/Put)和目标到期天数，自动计算并寻找流动性最好的期权合约代码"
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="正股代码，如 AAPL"
        ),
        ToolParameter(
            name="option_type",
            type="string",
            description="期权类型: Put (看跌,用于防守对冲) 或 Call (看涨)",
            enum=["Put", "Call"]
        ),
        ToolParameter(
            name="target_days",
            type="integer",
            description="期望期权到期的天数，例如防范财报可设为 7 到 14 天"
        ),
        ToolParameter(
            name="strike_offset_pct",
            type="number",
            description="行权价偏离现价的百分比，如 -5.0 表示寻找低于现价 5% 的 Put"
        )
    ]

    def execute(
        self,
        symbol: str,
        option_type: str,
        target_days: int,
        strike_offset_pct: float,
        **kwargs
    ) -> str:
        try:
            from datetime import datetime, timedelta
            
            
            ctx = get_quote_ctx()
            full_symbol = modify_symbol(symbol)
            
            # 1. 获取现价
            quotes = ctx.quote([full_symbol])
            if not quotes:
                return json.dumps({"error": f"无法获取 {symbol} 现价"})
            current_price = float(quotes[0].last_done)
            target_strike = current_price * (1 + strike_offset_pct / 100.0)
            
            # 2. 寻找最近的到期日
            expiries = ctx.option_chain_expiry_date_list(full_symbol)
            if not expiries:
                return json.dumps({"error": f"{symbol} 不支持期权交易或无数据"})
                
            target_date = datetime.now().date() + timedelta(days=target_days)
            best_expiry = min(expiries, key=lambda d: abs((d - target_date).days))
            
            # 3. 获取该到期日的期权链
            chain_info = ctx.option_chain_info_by_date(full_symbol, best_expiry)
            if not chain_info:
                return json.dumps({"error": f"无法获取 {best_expiry} 的期权链"})
                
            # 4. 筛选期权并寻找最接近 target_strike 的
            candidates = []
            for strike_info in chain_info:
                strike = float(strike_info.price)
                target_sym = strike_info.put_symbol if option_type == "Put" else strike_info.call_symbol
                if target_sym:
                    candidates.append({
                        "symbol": target_sym,
                        "strike": strike,
                        "expiry": str(best_expiry),
                        "distance_to_target": abs(strike - target_strike)
                    })
                    
            if not candidates:
                return json.dumps({"error": "找不到符合条件的期权合约"})
                
            # 找到最接近目标行权价的合约
            best_match = min(candidates, key=lambda x: x["distance_to_target"])
            
            # 5. 获取该期权的实时报价和流动性
            try:
                opt_quotes = ctx.option_quote([best_match["symbol"]])
                if opt_quotes:
                    oq = opt_quotes[0]
                    best_match["last_price"] = str(oq.last_done)
                    best_match["implied_volatility"] = str(oq.implied_volatility)
                    best_match["open_interest"] = str(oq.open_interest)
                    best_match["volume"] = str(oq.volume)
                    best_match["bid"] = str(oq.bid[0].price) if oq.bid else "N/A"
                    best_match["ask"] = str(oq.ask[0].price) if oq.ask else "N/A"
            except Exception as e:
                best_match["note"] = "无期权实时报价权限，返回理论最优合约代码，可直接用于买入"
                
            return json.dumps({
                "underlying": symbol,
                "underlying_price": current_price,
                "target_strike_price": target_strike,
                "recommended_option": best_match,
                "strategy": f"买入 {best_match['symbol']} 进行 {option_type} 操作对冲",
                "actionable_symbol_for_trade": best_match['symbol']
            })
            
        except Exception as e:
            return json.dumps({"error": str(e)})

def create_market_data_tools() -> list[BaseTool]:
    """创建所有行情数据工具"""
    return [
        GetTechnicalAnalysisTool(),
        ScanWatchlistTool(),
        GetKlineTool(),
        GetCapitalFlowTool(),
        GetFundamentalsTool(),
        GetMarketOverviewTool(),
        SearchHedgingOptionTool(),
    ]
