import asyncio
import pandas as pd
import numpy as np
import logging
import json
from datetime import datetime, timedelta

from tools.market_data import (
    get_quote_ctx, modify_symbol, calc_sma, calc_rsi, calc_macd, calc_avg_volume
)
from longport.openapi import Period, AdjustType

logger = logging.getLogger("quant_analyst")

# 11个核心行业 ETF
SECTOR_ETFS = {
    "Technology": "XLK",
    "Consumer Discretionary": "XLY",
    "Financials": "XLF",
    "Utilities": "XLU",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Consumer Staples": "XLP",
    "Real Estate": "XLRE",
    "Communication Services": "XLC"
}

class QuantAnalyst:
    """
    量化分析专家：负责提取真实的技术因子、行业相对强度(Sector RS)并生成真实的量化评分
    """
    def __init__(self):
        # 保持 Qlib 初始化逻辑，防破坏原设计（静默忽略错误）
        try:
            import qlib
            qlib.init(provider_uri="data/qlib_data", region="us")
        except Exception as e:
            logger.debug(f"Qlib init error (expected if local db is empty): {e}")

    def get_alpha_scores(self, tickers: list) -> dict:
        """
        获取一组股票的真实量化技术评分和因子
        """
        results = {}
        logger.info(f"正在为 {tickers} 生成真实量化技术评分...")
        
        try:
            ctx = get_quote_ctx()
        except Exception as e:
            logger.error(f"获取 QuoteContext 失败: {e}")
            return {t: self._get_fallback_data(t) for t in tickers}

        # 1. 先拉取 SPY 日线作为基准
        spy_closes = []
        try:
            spy_candles = ctx.history_candlesticks_by_offset(
                modify_symbol("SPY"), Period.Day, AdjustType.ForwardAdjust, False, 250
            )
            if spy_candles:
                spy_closes = [float(c.close) for c in spy_candles]
        except Exception as e:
            logger.error(f"拉取基准 SPY 历史 K 线失败: {e}")

        # 2. 为每只个股计算技术因子并打分
        for ticker in tickers:
            try:
                sym = modify_symbol(ticker)
                # 拉取 250 根日 K 线（支撑 200 日均线计算）
                daily = ctx.history_candlesticks_by_offset(
                    sym, Period.Day, AdjustType.ForwardAdjust, False, 250
                )
                if not daily or len(daily) < 50:
                    results[ticker] = self._get_fallback_data(ticker)
                    continue

                closes = [float(c.close) for c in daily]
                highs = [float(c.high) for c in daily]
                lows = [float(c.low) for c in daily]
                volumes = [int(c.volume) for c in daily]
                price = closes[-1]

                # 均线计算
                sma50 = calc_sma(closes, 50)
                sma200 = calc_sma(closes, 200)

                # RSI与MACD计算
                rsi = calc_rsi(closes, 14) or 50.0
                macd_data = calc_macd(closes)
                macd_hist = macd_data.get("histogram", 0) if macd_data else 0

                # 量比计算（最新量 / 50日均量）
                avg_vol = calc_avg_volume(daily, 50)
                vol_ratio = round(volumes[-1] / avg_vol, 2) if avg_vol and avg_vol > 0 else 1.0

                # 20日涨跌幅与 RS 相比大盘强度
                ret_20d = round((price / closes[-20] - 1) * 100, 2) if len(closes) >= 20 else 0.0
                rs_ratio_20d = 1.0
                if len(closes) >= 20 and len(spy_closes) >= 20:
                    stock_ret = closes[-1] / closes[-20]
                    spy_ret = spy_closes[-1] / spy_closes[-20]
                    rs_ratio_20d = round(stock_ret / spy_ret, 4) if spy_ret > 0 else 1.0

                # ═══════════════════════════════════════
                # 真实技术打分规则（True Alpha Score，总分100）
                # ═══════════════════════════════════════
                score = 0

                # 1. 均线趋势对齐（最大 30分）
                if sma50 and sma200:
                    if price > sma50 > sma200:
                        score += 30  # 标准 Stage 2 趋势
                    elif price > sma50:
                        score += 15  # 底部反转 Stage 1
                    elif price > sma200:
                        score += 10
                else:
                    if sma50 and price > sma50:
                        score += 20

                # 2. RSI强弱区间（最大 25分）
                if 50 <= rsi <= 70:
                    score += 25  # 强势上升极佳区间
                elif 40 <= rsi < 50:
                    score += 15  # 整理盘
                elif rsi > 70:
                    score += 15  # 略微超买但属于强势加速段
                else:
                    score += 5   # 弱势

                # 3. 相对大盘强度 (RS Ratio)（最大 30分）
                if rs_ratio_20d >= 1.05:
                    score += 30  # 领先大盘 5%+
                elif 1.0 <= rs_ratio_20d < 1.05:
                    score += 20  # 优于大盘
                elif 0.95 <= rs_ratio_20d < 1.0:
                    score += 10  # 略输大盘
                else:
                    score += 0

                # 4. 活跃度与资金量比（最大 15分）
                if vol_ratio >= 1.5:
                    score += 15  # 爆量建仓/突破
                elif 1.0 <= vol_ratio < 1.5:
                    score += 10  # 正常活跃
                else:
                    score += 5

                # 判定 Trend_Stage 阶段
                trend_stage = "Stage 4"
                if sma50 and sma200:
                    if price > sma50 > sma200:
                        trend_stage = "Stage 2"
                    elif price > sma50:
                        trend_stage = "Stage 1"
                    elif price < sma50 and price < sma200:
                        trend_stage = "Stage 4"
                    else:
                        trend_stage = "Stage 3"

                results[ticker] = {
                    "score": float(score),
                    "signals": {
                        "RSI": "Overbought" if rsi > 70 else ("Oversold" if rsi < 30 else "Neutral"),
                        "RSI_Value": round(rsi, 2),
                        "Momentum": "Strong" if macd_hist > 0 else "Weak",
                        "Trend_Stage": trend_stage,
                        "Volume_Ratio": vol_ratio,
                        "Return_20d_Pct": ret_20d,
                        "RS_vs_SPY_20d": rs_ratio_20d
                    },
                    "recommendation": "BUY" if (score >= 60 and rsi < 70) else "HOLD"
                }

            except Exception as e:
                logger.error(f"分析 {ticker} 真实因子失败: {e}")
                results[ticker] = self._get_fallback_data(ticker)

        return results

    def get_sector_strength(self) -> dict:
        """
        计算 11 个行业 ETF 相对于大盘 SPY 的相对强度 (Sector Relative Strength)
        返回按 20 日强度降序排列的行业名称和强度指标
        """
        logger.info("计算 11 个行业 ETF 的相对大盘强度 (Sector RS)...")
        sector_scores = {}
        
        try:
            ctx = get_quote_ctx()
            # 获取标杆 SPY K线
            spy_candles = ctx.history_candlesticks_by_offset(
                modify_symbol("SPY"), Period.Day, AdjustType.ForwardAdjust, False, 30
            )
            if not spy_candles or len(spy_candles) < 20:
                raise ValueError("SPY K线数据不足，无法计算行业强度")
            spy_closes = [float(c.close) for c in spy_candles]
            spy_ret_20d = spy_closes[-1] / spy_closes[-20] if spy_closes[-20] > 0 else 1.0

            for sector_name, etf_symbol in SECTOR_ETFS.items():
                try:
                    etf_sym = modify_symbol(etf_symbol)
                    candles = ctx.history_candlesticks_by_offset(
                        etf_sym, Period.Day, AdjustType.ForwardAdjust, False, 30
                    )
                    if not candles or len(candles) < 20:
                        sector_scores[sector_name] = 1.0
                        continue
                    
                    closes = [float(c.close) for c in candles]
                    etf_ret_20d = closes[-1] / closes[-20]
                    # 计算相对强度
                    rs_ratio = round(etf_ret_20d / spy_ret_20d, 4)
                    sector_scores[sector_name] = rs_ratio
                except Exception as e:
                    logger.error(f"计算行业 {sector_name} ({etf_symbol}) 强度出错: {e}")
                    sector_scores[sector_name] = 1.0

        except Exception as e:
            logger.error(f"行业强度计算整体流程出错: {e}")
            # 默认返回平价
            sector_scores = {s: 1.0 for s in SECTOR_ETFS}

        # 按相对强度降序排序
        sorted_sectors = dict(sorted(sector_scores.items(), key=lambda item: item[1], reverse=True))
        logger.info(f"行业强度计算完成，最强主线为: {list(sorted_sectors.items())[:4]}")
        return sorted_sectors

    def _get_fallback_data(self, ticker: str) -> dict:
        """数据拉取失败时的保底回退数据"""
        return {
            "score": 50.0,
            "signals": {
                "RSI": "Neutral",
                "RSI_Value": 50.0,
                "Momentum": "Weak",
                "Trend_Stage": "Stage 1",
                "Volume_Ratio": 1.0,
                "Return_20d_Pct": 0.0,
                "RS_vs_SPY_20d": 1.0
            },
            "recommendation": "HOLD"
        }

    def get_factor_report(self, ticker: str) -> str:
        """
        生成详细的量化因子报告，供 LLM 参考
        """
        scores = self.get_alpha_scores([ticker])
        data = scores.get(ticker, {})
        
        report = f"--- Quant Analysis for {ticker} ---\n"
        report += f"Real Technical Factor Score: {data.get('score')}/100\n"
        report += f"Technical Signals: {json.dumps(data.get('signals'))}\n"
        report += f"Strategy Suggestion: {data.get('recommendation')}\n"
        return report

# 用于工具集成
def create_quant_tools():
    from tools.base import Tool
    
    analyst = QuantAnalyst()
    
    class GetQuantScoreTool(Tool):
        name = "get_quant_score"
        description = "获取股票的量化因子评分和技术面信号（基于真实行情及个股相对SPY强度）"
        def execute(self, tickers: list):
            return json.dumps(analyst.get_alpha_scores(tickers))

    return [GetQuantScoreTool()]
