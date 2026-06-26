import asyncio
import json
import logging
import os
from datetime import datetime
import pytz

from llm.base import BaseLLM, ChatMessage, Role
from tools.search import get_search_client
from config import DEFAULT_WATCHLIST
from agent.quant_analyst import QuantAnalyst, SECTOR_ETFS
from data.trade_logger import get_trade_logger

logger = logging.getLogger("AlphaScanner")

# 50只横跨美股各主线板块的明星股池 (Golden Universe)
GOLDEN_UNIVERSE = {
    # 科技 / 半导体
    "NVDA": "Technology", "TSM": "Technology", "AVGO": "Technology", "MSFT": "Technology",
    "AAPL": "Technology", "AMD": "Technology", "PLTR": "Technology", "QCOM": "Technology",
    "ASML": "Technology", "ARM": "Technology", "NOW": "Technology", "CRM": "Technology",
    # 通信服务
    "GOOGL": "Communication Services", "META": "Communication Services", "NFLX": "Communication Services",
    # 周期消费 / 电商
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "CMG": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    # 金融 / 支付 / 信用
    "JPM": "Financials", "V": "Financials", "MA": "Financials", "MS": "Financials",
    "GS": "Financials", "SPGI": "Financials", "HOOD": "Financials",
    # 工业 / 制造 / 散热 / 航空
    "GE": "Industrials", "CAT": "Industrials", "LMT": "Industrials", "WM": "Industrials",
    "VRT": "Industrials",
    # 公用事业 / AI核电电力
    "CEG": "Utilities", "VST": "Utilities", "NEE": "Utilities",
    # 医疗 / 医药 / 手术机器人
    "LLY": "Healthcare", "NVO": "Healthcare", "ISRG": "Healthcare", "UNH": "Healthcare",
    "PFE": "Healthcare", "AMGN": "Healthcare",
    # 防守消费
    "KO": "Consumer Staples", "PEP": "Consumer Staples", "COST": "Consumer Staples", "PG": "Consumer Staples",
    # 传统能源
    "XOM": "Energy", "CVX": "Energy",
    # 原材料
    "FCX": "Materials", "NEM": "Materials"
}

class AlphaScanner:
    """
    Phase 0: 盘前主线扫描 (Alpha Scanner) v3.5
    用于在每天开盘前，采用 Top-Down（自上而下）方式，结合行业相对强度、个股真实技术评分、财报避雷和催化剂检索，
    动态挑选出 10-15 只最强标的更新为今日的监控标的池 (WATCHLIST)。
    """
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.search_client = get_search_client()
        self.quant_analyst = QuantAnalyst()
        self.trade_logger = get_trade_logger()
        self.last_metadata = {}

    def run(self) -> tuple[list, str]:
        logger.info("[Phase 0] 启动 Alpha Scanner v3.5 进行科学选股...")
        
        # 1. 获取当前账户持仓（持仓股必须强制入选扫描和监控）
        holdings = self._get_current_holdings()
        logger.info(f"当前账户持仓: {holdings}")

        # 2. 行业相对强度过滤 (Sector RS)
        sector_ranks = self.quant_analyst.get_sector_strength()
        top_sectors = list(sector_ranks.keys())[:4]  # 选取相对强度最强的 4 个主线行业
        logger.info(f"Top-Down 锁定最强主线行业: {top_sectors}")

        # 3. 计算 50 只个股的真实因子评分 (Qlib/技术多因子)
        universe_tickers = list(GOLDEN_UNIVERSE.keys())
        # 保证持仓股如果在 Universe 之外也会被加入评分
        all_scan_tickers = list(set(universe_tickers + holdings))
        
        quant_results = self.quant_analyst.get_alpha_scores(all_scan_tickers)

        # 4. 科学过滤筛选
        filtered_candidates = []
        for ticker, data in quant_results.items():
            score = data.get("score", 50.0)
            sector = GOLDEN_UNIVERSE.get(ticker, "Unknown")
            
            # 筛选准则：
            # A. 属于持仓股（必须保留）
            # B. 属于最强板块，且评分 >= 50 (板块顺势)
            # C. 不属于最强板块，但评分 >= 75 (特异爆发黑马)
            if ticker in holdings:
                filtered_candidates.append((ticker, score, "HOLDING"))
            elif sector in top_sectors and score >= 50:
                filtered_candidates.append((ticker, score, "SECTOR_TREND"))
            elif score >= 75:
                filtered_candidates.append((ticker, score, "IDIOSYNCRATIC_BREAKOUT"))

        # 按技术评分降序排列
        filtered_candidates.sort(key=lambda x: x[1], reverse=True)
        
        # 取评分最高的前 15 只股票（含持仓股）作为最终研判候选
        top_candidates = []
        seen = set()
        # 优先把持仓股放进来
        for item in filtered_candidates:
            if item[2] == "HOLDING":
                top_candidates.append(item)
                seen.add(item[0])
                
        for item in filtered_candidates:
            if item[0] not in seen and len(top_candidates) < 15:
                top_candidates.append(item)
                seen.add(item[0])

        candidate_tickers = [c[0] for c in top_candidates]
        logger.info(f"通过多因子初步筛选出 15 只高胜率候选个股: {candidate_tickers}")

        # 5. 精细化搜索候选股的财报日期与催化剂（节省大模型上下文并极速避雷）
        search_results = ""
        if candidate_tickers:
            # 分组进行搜索，避免大量个股单独搜索造成高延迟
            group_size = 5
            for i in range(0, len(candidate_tickers), group_size):
                sub_group = candidate_tickers[i:i+group_size]
                query = f"next earnings date for {' '.join(sub_group)} major catalyst news 2026"
                try:
                    res = self.search_client.search(query)
                    search_results += f"\nQuery: {query}\nResults:\n{res}\n"
                except Exception as e:
                    logger.error(f"Group search failed for {sub_group}: {e}")

        # 获取最新风控评分，用于方案 A 的动态放宽 RSI/估值选股限制
        latest_risk = self.trade_logger.get_latest_risk_score()
        risk_score = latest_risk["score"] if latest_risk else 75.0
        risk_regime = latest_risk["regime"] if latest_risk else "favorable"

        # 6. 自动对冲标的入池 (Hedge Auto-Inclusion)
        hedge_symbols = []
        if risk_score < 70:
            logger.info(f"由于风险评分较差 ({risk_score}), 启动自动对冲标的检索...")
            try:
                from tools.market_data import SearchHedgingOptionTool
                hedge_tool = SearchHedgingOptionTool()
                # 检索 SPY 和 QQQ 的对冲 Put
                for index_sym in ["SPY", "QQQ"]:
                    res_json = hedge_tool.execute(
                        symbol=index_sym,
                        option_type="Put",
                        target_days=14,
                        strike_offset_pct=-3.0
                    )
                    res_data = json.loads(res_json)
                    if "option_symbol" in res_data:
                        opt_sym = res_data["option_symbol"]
                        hedge_symbols.append(opt_sym)
                        logger.info(f"已自动添加对冲标的: {opt_sym}")
            except Exception as e:
                logger.error(f"自动对冲检索失败: {e}")

        # 7. 构造 LLM 催化剂避雷与优中选优 Prompt
        system_prompt = f"""你是一个顶级对冲基金的 Alpha 选股分析师。
你的任务是根据提供的技术候选个股列表、各股真实量化评分，并结合最近的财报日程和催化剂新闻，精选出 10-12 只高概率的美股标的组成今日监控标的池。

【今日宏观风控环境】：
- 宏观风控评分: {risk_score}/100
- 风险象限状态: {risk_regime.upper()}

【建议的自动对冲标的】：
{json.dumps(hedge_symbols)} (如果风险评分较低，请必须包含这些对冲期权)

【必选持仓股】（这些是你当前持有的股票，**必须**强制入选最终的监控池以确保监控）：
{json.dumps(holdings)}

【技术优选候选股（含真实量化多因子评分）】：
{json.dumps({c[0]: {"score": c[1], "type": c[2], "sector": GOLDEN_UNIVERSE.get(c[0], "Unknown")} for c in top_candidates}, indent=2)}

【检索到的财报日程与催化剂动态】：
{search_results}

【选股与排除硬约束】：
1. 财报雷区避让：如果任何非持仓股在**未来5个交易日内**将发布财报，请必须将其**排除**（财报前属于开盲盒，风控不准买入）。
2. 持仓包含约束：你输出的 watchlist 中**必须**强制包含全部的持仓股（即上面的持仓股：{holdings}）。
3. 风险对冲：若【今日宏观风控环境】分值 < 70，请必须在最终名单中包含上述建议的对冲期权。
4. 动态超买与估值松绑法则（方案 A）：
   - 当【今日宏观风控环境】处于 **FAVORABLE** (分值 > 75) 极佳牛市多头状态时，市场风险偏好处于高位，说明市场以动能主升为主。对于虽然技术面有超买（如 RSI 在 70-80 区间）或估值很高（Very Overvalued，如 ARM 等半导体/AI龙头），但技术多因子评分极高且有近期明确爆发催化剂的动能股，**绝对允许并且应当将其纳入今日标的池**，以便高频监控 and 捕获强势上涨主段！不要因为估值恐高或指标超买而一刀切将动能龙头剔除。
   - 当处于 **CAUTIOUS** 或 **LOCKDOWN** 状态时，必须严守防守硬约束，坚决把任何估值极度高估或指标处于超买高位的个股排除出去，以防大盘回调时在高位接盘。
5. 选出真正具有近期重大上涨催化剂（如研报调级、技术面突破、重要大合同、行业风口）的标的。

输出格式要求：
请仅返回纯 JSON 格式。除了 watchlist (代码列表)，还必须为每只股票提供元数据。
格式必须为：
{{
    "sectors": ["主线板块1", "主线板块2"],
    "watchlist_detail": {{
        "AAPL": {{"score": 85, "rank": 1, "conviction": "Tier 1", "reason": "..."}},
        "NVDA": {{"score": 92, "rank": 2, "conviction": "Tier 1", "reason": "..."}},
        ...
    }},
    "reasoning": "结合真实技术评分和财报日程的精选逻辑"
}}
"""
        messages = [
            ChatMessage(role=Role.SYSTEM, content=system_prompt),
            ChatMessage(role=Role.USER, content="请根据以上信息进行精细化评估，规避近期财报股，产出今日最终最科学的标的池。")
        ]

        try:
            logger.info("正在请大模型进行催化剂判别与财报日程排雷...")
            response = self.llm.chat(messages)
            content = response.content.strip()
            
            # 正则清理并提取 JSON
            import re
            match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                json_str = match.group(1)
            else:
                match = re.search(r'(\{.*\})', content, re.DOTALL)
                json_str = match.group(1) if match else content
                
            data = json.loads(json_str.strip())
            watchlist_detail = data.get("watchlist_detail", {})
            
            # 8. 纯代码兜底回填：再次强制保证 holdings 和对冲标的 100% 被纳入
            for h in holdings:
                if h not in watchlist_detail:
                    watchlist_detail[h] = {"score": 50, "rank": 99, "conviction": "Tier 2", "reason": "Existing Holding"}
            
            for s in hedge_symbols:
                if s not in watchlist_detail:
                    watchlist_detail[s] = {"score": 100, "rank": 0, "conviction": "Tier 1", "reason": "Macro Hedge"}

            # 提取最终列表用于后向兼容
            watchlist = list(watchlist_detail.keys())
                
            logger.info(f"科学选股完成！包含 {len(watchlist)} 只标的。")
            
            self._save_watchlist(watchlist, watchlist_detail, data.get("reasoning", ""))
            return watchlist, data.get("reasoning", "基于行业轮动 RS、个股真实 K 线因子及 5 日财报排雷精选。")
            
        except Exception as e:
            fallback_watchlist = list(set(DEFAULT_WATCHLIST + holdings + hedge_symbols))
            fallback_detail = {s: {"score": 50, "rank": 50, "conviction": "Tier 2", "reason": "Fallback"} for s in fallback_watchlist}
            error_msg = f"Alpha Scanner 科学选股异常: {e}，将回退并强制合并持仓。"
            logger.error(f"{error_msg}")
            self._save_watchlist(fallback_watchlist, fallback_detail, "Alpha Scanner failed, fallback to default + holdings")
            return fallback_watchlist, error_msg

    def _get_current_holdings(self) -> list:
        try:
            from tools.trading import GetPositionsTool
            tool = GetPositionsTool()
            res_str = tool.execute()
            data = json.loads(res_str)
            if "positions" in data:
                holdings = [pos["symbol"] for pos in data["positions"] if float(pos.get("quantity", 0)) > 0]
                logger.info(f"成功获取当前持仓: {holdings}")
                return holdings
        except Exception as e:
            logger.error(f"获取当前持仓失败: {e}")
        return []

    def _save_watchlist(self, watchlist: list, watchlist_detail: dict, reason: str):
        today_str = datetime.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d")
        data = {
            "date": today_str,
            "watchlist": watchlist,
            "watchlist_detail": watchlist_detail,
            "reason": reason,
            "updated_at": datetime.now(pytz.timezone("US/Eastern")).isoformat()
        }
        
        os.makedirs("data", exist_ok=True)
        with open("data/daily_watchlist.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"今日动态标的池已保存至 data/daily_watchlist.json")
