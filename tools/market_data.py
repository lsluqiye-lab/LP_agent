"""
行情数据工具集
基于 LongPort QuoteContext 提供技术分析、K线、资金流向等数据
这些数据直接来自交易所，不依赖外部搜索引擎
"""
import json
import math
import logging
from datetime import datetime, timedelta
from typing import Optional

from longport.openapi import (
    Config, QuoteContext, Period, AdjustType, CalcIndex
)

from tools.base import BaseTool, ToolParameter


logger = logging.getLogger("MarketData")


# ───────────────────────────────────────────
# 共享 QuoteContext 单例（避免重复创建连接）
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
# 辅助计算函数
# ───────────────────────────────────────────

def calc_sma(prices: list[float], period: int) -> Optional[float]:
    """计算简单移动平均线"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calc_ema(prices: list[float], period: int) -> Optional[float]:
    """计算指数移动平均线"""
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period  # 初始 SMA 作为种子
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """
    计算 RSI（Relative Strength Index）
    使用 Wilder 平滑法（与大多数交易软件一致）
    """
    if len(prices) < period + 1:
        return None

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    # 初始平均值
    gains = [d if d > 0 else 0 for d in deltas[:period]]
    losses = [-d if d < 0 else 0 for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder 平滑
    for d in deltas[period:]:
        gain = d if d > 0 else 0
        loss = -d if d < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_avg_volume(candles, period: int) -> Optional[float]:
    """计算近 N 根K线的平均成交量"""
    vols = [int(c.volume) for c in candles]
    if len(vols) < period:
        return None
    return sum(vols[-period:]) / period


# ═══════════════════════════════════════════
# 工具1: 技术分析（核心工具，一次返回所有中长线关键指标）
# ═══════════════════════════════════════════

class GetTechnicalAnalysisTool(BaseTool):
    """
    获取个股完整技术分析报告
    一次调用返回中长线策略所需的全部关键指标
    """

    name = "get_technical_analysis"
    description = (
        "获取个股完整技术分析报告，包含：均线系统（SMA20/50/200及金叉判断）、"
        "RSI(14)、成交量分析（当日量 vs 50日均量）、相对强度（vs SPY）、"
        "Stage 2上升趋势判断。这是中长线决策的核心工具。"
    )
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码，如 AAPL、TSLA、NVDA 等"
        )
    ]

    def execute(self, symbol: str, **kwargs) -> str:
        try:
            ctx = get_quote_ctx()
            sym = modify_symbol(symbol)

            # ── 1. 获取日K线（250根，覆盖200日均线）──
            daily = ctx.history_candlesticks_by_offset(
                sym, Period.Day, AdjustType.ForwardAdjust,
                forward=False, count=250
            )
            if not daily or len(daily) < 50:
                return json.dumps({"error": f"{symbol} 日K线数据不足（需至少50根）"}, ensure_ascii=False)

            closes_d = [float(c.close) for c in daily]
            latest_price = closes_d[-1]

            # ── 2. 均线系统 ──
            sma20 = calc_sma(closes_d, 20)
            sma50 = calc_sma(closes_d, 50)
            sma200 = calc_sma(closes_d, 200)

            ma_analysis = {
                "SMA20": round(sma20, 2) if sma20 else None,
                "SMA50": round(sma50, 2) if sma50 else None,
                "SMA200": round(sma200, 2) if sma200 else None,
                "price_above_SMA50": latest_price > sma50 if sma50 else None,
                "price_above_SMA200": latest_price > sma200 if sma200 else None,
                "golden_cross": sma50 > sma200 if (sma50 and sma200) else None,
            }

            # Stage 2 判断: 价格>SMA50>SMA200
            stage2 = False
            if sma50 and sma200:
                stage2 = latest_price > sma50 and sma50 > sma200

            # ── 3. RSI（日线和周线）──
            rsi_daily = calc_rsi(closes_d, 14)

            # 周K线 RSI
            weekly = ctx.history_candlesticks_by_offset(
                sym, Period.Week, AdjustType.ForwardAdjust,
                forward=False, count=30
            )
            closes_w = [float(c.close) for c in weekly] if weekly else []
            rsi_weekly = calc_rsi(closes_w, 14)

            rsi_analysis = {
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
            }

            # ── 4. 成交量分析 ──
            avg_vol_50 = calc_avg_volume(daily, 50)
            latest_vol = int(daily[-1].volume)
            vol_ratio = round(latest_vol / avg_vol_50, 2) if avg_vol_50 and avg_vol_50 > 0 else None

            volume_analysis = {
                "latest_volume": latest_vol,
                "avg_volume_50d": int(avg_vol_50) if avg_vol_50 else None,
                "volume_ratio": vol_ratio,
                "is_high_volume": vol_ratio >= 1.5 if vol_ratio else None,
            }

            # ── 5. 相对强度 vs SPY ──
            rs_vs_spy = None
            try:
                spy_daily = ctx.history_candlesticks_by_offset(
                    "SPY.US", Period.Day, AdjustType.ForwardAdjust,
                    forward=False, count=60
                )
                if spy_daily and len(spy_daily) >= 20:
                    spy_closes = [float(c.close) for c in spy_daily]
                    # 20日相对表现
                    stock_20d_return = (closes_d[-1] / closes_d[-20] - 1) * 100 if len(closes_d) >= 20 else None
                    spy_20d_return = (spy_closes[-1] / spy_closes[-20] - 1) * 100 if len(spy_closes) >= 20 else None
                    # 50日相对表现
                    stock_50d_return = (closes_d[-1] / closes_d[-50] - 1) * 100 if len(closes_d) >= 50 else None
                    spy_50d_return = (spy_closes[-1] / spy_closes[-50] - 1) * 100 if len(spy_closes) >= 50 else None

                    rs_vs_spy = {
                        "stock_20d_return_pct": round(stock_20d_return, 2) if stock_20d_return is not None else None,
                        "SPY_20d_return_pct": round(spy_20d_return, 2) if spy_20d_return is not None else None,
                        "outperform_SPY_20d": stock_20d_return > spy_20d_return if (stock_20d_return is not None and spy_20d_return is not None) else None,
                        "stock_50d_return_pct": round(stock_50d_return, 2) if stock_50d_return is not None else None,
                        "SPY_50d_return_pct": round(spy_50d_return, 2) if spy_50d_return is not None else None,
                        "outperform_SPY_50d": stock_50d_return > spy_50d_return if (stock_50d_return is not None and spy_50d_return is not None) else None,
                    }
            except Exception as e:
                logger.warning(f"获取SPY数据失败: {e}")

            # ── 6. 价格区间与波动 ──
            high_52w = max(closes_d[-min(250, len(closes_d)):])
            low_52w = min(closes_d[-min(250, len(closes_d)):])
            from_52w_high_pct = round((latest_price / high_52w - 1) * 100, 2)
            from_52w_low_pct = round((latest_price / low_52w - 1) * 100, 2)

            price_info = {
                "latest_price": latest_price,
                "52w_high": round(high_52w, 2),
                "52w_low": round(low_52w, 2),
                "pct_from_52w_high": from_52w_high_pct,
                "pct_from_52w_low": from_52w_low_pct,
            }

            # ── 7. 止损参考价位 ──
            # 近期20日波段低点
            recent_lows = [float(c.low) for c in daily[-20:]]
            swing_low_20d = round(min(recent_lows), 2)
            stop_loss_8pct = round(latest_price * 0.92, 2)
            stop_loss_12pct = round(latest_price * 0.88, 2)

            stop_loss_ref = {
                "stop_loss_8pct": stop_loss_8pct,
                "stop_loss_12pct": stop_loss_12pct,
                "swing_low_20d": swing_low_20d,
                "SMA50_as_stop": round(sma50, 2) if sma50 else None,
            }

            # ── 汇总 ──
            result = {
                "symbol": cut_symbol(sym),
                "stage2_uptrend": stage2,
                "price": price_info,
                "moving_averages": ma_analysis,
                "rsi": rsi_analysis,
                "volume": volume_analysis,
                "relative_strength_vs_SPY": rs_vs_spy,
                "stop_loss_reference": stop_loss_ref,
                "summary": self._generate_summary(
                    symbol, latest_price, stage2, ma_analysis,
                    rsi_analysis, volume_analysis, rs_vs_spy
                ),
            }

            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _generate_summary(self, symbol, price, stage2, ma, rsi, vol, rs):
        """生成简洁的文字摘要"""
        lines = [f"{symbol} @ ${price:.2f}"]

        # 趋势
        if stage2:
            lines.append("✅ 处于 Stage 2 上升趋势（价格>SMA50>SMA200）")
        else:
            reasons = []
            if ma.get("price_above_SMA50") is False:
                reasons.append("价格 < SMA50")
            if ma.get("price_above_SMA200") is False:
                reasons.append("价格 < SMA200")
            if ma.get("golden_cross") is False:
                reasons.append("SMA50 < SMA200（死叉）")
            lines.append(f"❌ 未达 Stage 2 条件: {', '.join(reasons) if reasons else '数据不足'}")

        # RSI
        zone = rsi.get("weekly_RSI_zone", "unknown")
        rsi_val = rsi.get("RSI_weekly_14")
        zone_desc = {
            "strong": "强势区（适合建仓）",
            "overbought_danger": "严重超买（停止建仓！）",
            "weak_avoid": "弱势区（绝不买入！）",
            "neutral": "中性区",
            "elevated": "偏高（谨慎）",
        }
        lines.append(f"RSI周线: {rsi_val} — {zone_desc.get(zone, zone)}")

        # 量
        if vol.get("is_high_volume"):
            lines.append(f"放量 {vol['volume_ratio']}x（关注突破信号）")
        else:
            lines.append(f"量比 {vol.get('volume_ratio', 'N/A')}x（正常）")

        # 相对强度
        if rs and rs.get("outperform_SPY_20d") is not None:
            if rs["outperform_SPY_20d"]:
                lines.append(f"✅ 20日跑赢SPY（{rs['stock_20d_return_pct']}% vs {rs['SPY_20d_return_pct']}%）")
            else:
                lines.append(f"❌ 20日跑输SPY（{rs['stock_20d_return_pct']}% vs {rs['SPY_20d_return_pct']}%）")

        return " | ".join(lines)


# ═══════════════════════════════════════════
# 工具2: K线数据
# ═══════════════════════════════════════════

class GetKlineTool(BaseTool):
    """获取K线/蜡烛图数据"""

    name = "get_kline"
    description = (
        "获取指定股票的K线数据（蜡烛图），支持日线、周线、月线等多种周期。"
        "返回开高低收、成交量等原始数据，适合需要查看价格走势细节的场景。"
    )
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码，如 AAPL、NVDA 等"
        ),
        ToolParameter(
            name="period",
            type="string",
            description="K线周期: day(日线)、week(周线)、month(月线)、min_60(60分钟)",
            enum=["day", "week", "month", "min_60"],
            default="day",
            required=False,
        ),
        ToolParameter(
            name="count",
            type="integer",
            description="获取根数，默认50",
            required=False,
            default=50,
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
            for c in candles[-count:]:  # 只取最后 count 根
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
# 工具3: 资金流向分析
# ═══════════════════════════════════════════

class GetCapitalFlowTool(BaseTool):
    """获取资金流向与大中小单分布"""

    name = "get_capital_flow"
    description = (
        "获取个股的资金流向数据，包括今日净流入/流出趋势，"
        "以及大单、中单、小单的资金分布情况，用于判断主力资金动向。"
    )
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码，如 AAPL、NVDA 等"
        )
    ]

    def execute(self, symbol: str, **kwargs) -> str:
        try:
            ctx = get_quote_ctx()
            sym = modify_symbol(symbol)

            # 资金流向（分时）
            flow_data = ctx.capital_flow(sym)
            if not flow_data:
                return json.dumps({"error": f"{symbol} 无资金流向数据"}, ensure_ascii=False)

            # 取最后几个时间点的净流入
            latest_inflow = float(flow_data[-1].inflow) if flow_data else 0
            # 简化为最近30个数据点的趋势
            recent = flow_data[-30:] if len(flow_data) >= 30 else flow_data
            inflows = [float(f.inflow) for f in recent]
            trend = "inflow" if inflows[-1] > inflows[0] else "outflow"

            # 资金分布（大中小单）
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
                # 主力 = 大单
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
# 工具4: 基本面指标
# ═══════════════════════════════════════════

class GetFundamentalsTool(BaseTool):
    """获取股票基本面指标"""

    name = "get_fundamentals"
    description = (
        "获取股票的基本面指标：PE(TTM)、PB、总市值、换手率、"
        "5日/10日/半年/年初至今涨跌幅等，用于估值和涨幅评估。"
    )
    parameters = [
        ToolParameter(
            name="symbols",
            type="string",
            description="股票代码，支持多只股票用逗号分隔，如 AAPL,NVDA,TSLA"
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
                    "change_value": float(idx.change_value) if idx.change_value else None,
                    "volume": int(idx.volume) if idx.volume else None,
                    "turnover": float(idx.turnover) if idx.turnover else None,
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

            return json.dumps({
                "fundamentals": results,
                "count": len(results),
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


# ═══════════════════════════════════════════
# 工具5: 大盘环境分析
# ═══════════════════════════════════════════

class GetMarketOverviewTool(BaseTool):
    """获取大盘环境分析"""

    name = "get_market_overview"
    description = (
        "分析当前大盘环境：SPY 和 QQQ 的技术面状况（均线、RSI、成交量），"
        "判断大盘是否处于上升趋势，为中长线建仓/减仓提供背景判断。"
    )
    parameters = []

    def execute(self, **kwargs) -> str:
        try:
            ctx = get_quote_ctx()
            results = {}

            for index_symbol in ["SPY.US", "QQQ.US"]:
                short_name = cut_symbol(index_symbol)

                # 获取日K
                daily = ctx.history_candlesticks_by_offset(
                    index_symbol, Period.Day, AdjustType.ForwardAdjust,
                    forward=False, count=250
                )

                if not daily or len(daily) < 50:
                    results[short_name] = {"error": "K线数据不足"}
                    continue

                closes = [float(c.close) for c in daily]
                price = closes[-1]

                sma20 = calc_sma(closes, 20)
                sma50 = calc_sma(closes, 50)
                sma200 = calc_sma(closes, 200)

                rsi = calc_rsi(closes, 14)
                avg_vol = calc_avg_volume(daily, 50)
                latest_vol = int(daily[-1].volume)
                vol_ratio = round(latest_vol / avg_vol, 2) if avg_vol else None

                # 趋势判断
                uptrend = price > sma50 > sma200 if (sma50 and sma200) else None
                above_200 = price > sma200 if sma200 else None

                results[short_name] = {
                    "price": round(price, 2),
                    "SMA20": round(sma20, 2) if sma20 else None,
                    "SMA50": round(sma50, 2) if sma50 else None,
                    "SMA200": round(sma200, 2) if sma200 else None,
                    "price_above_SMA50": price > sma50 if sma50 else None,
                    "price_above_SMA200": above_200,
                    "golden_cross_SMA50_200": sma50 > sma200 if (sma50 and sma200) else None,
                    "uptrend": uptrend,
                    "RSI_14": rsi,
                    "volume_ratio": vol_ratio,
                }

            # 综合判断
            spy_ok = results.get("SPY", {}).get("uptrend", False)
            qqq_ok = results.get("QQQ", {}).get("uptrend", False)

            if spy_ok and qqq_ok:
                market_verdict = "bullish — 大盘多头排列，适合中长线建仓"
            elif spy_ok or qqq_ok:
                market_verdict = "mixed — 大盘信号分歧，建仓需谨慎"
            else:
                # 是否跌破200日
                spy_200 = results.get("SPY", {}).get("price_above_SMA200")
                if spy_200 is False:
                    market_verdict = "bearish — SPY 跌破200日均线，停止建仓并考虑减仓"
                else:
                    market_verdict = "neutral — 大盘横盘/弱势，等待信号"

            return json.dumps({
                "indexes": results,
                "market_verdict": market_verdict,
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


# ───────────────────────────────────────────
# 工具注册入口
# ───────────────────────────────────────────

def create_market_data_tools() -> list[BaseTool]:
    """
    创建所有行情数据工具实例

    Returns:
        行情数据工具列表
    """
    return [
        GetTechnicalAnalysisTool(),
        GetKlineTool(),
        GetCapitalFlowTool(),
        GetFundamentalsTool(),
        GetMarketOverviewTool(),
    ]
