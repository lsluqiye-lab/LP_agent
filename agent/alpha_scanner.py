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

        # 6. 构造 LLM 催化剂避雷与优中选优 Prompt
        system_prompt = f"""你是一个顶级对冲基金的 Alpha 选股分析师。
你的任务是根据提供的技术候选个股列表、各股真实量化评分，并结合最近的财报日程和催化剂新闻，精选出 10-12 只高概率的美股标的组成今日监控标的池。

【必选持仓股】（这些是你当前持有的股票，**必须**强制入选最终的监控池以确保监控）：
{json.dumps(holdings)}

【技术优选候选股（含真实量化多因子评分）】：
{json.dumps({c[0]: {"score": c[1], "type": c[2], "sector": GOLDEN_UNIVERSE.get(c[0], "Unknown")} for c in top_candidates}, indent=2)}

【检索到的财报日程与催化剂动态】：
{search_results}

【选股与排除硬约束】：
1. 财报雷区避让：如果任何非持仓股在**未来5个交易日内**将发布财报，请必须将其**排除**（财报前属于开盲盒，风控不准买入）。
2. 持仓包含约束：你输出的 watchlist 中**必须**强制包含全部的持仓股（即上面的持仓股：{holdings}）。
3. 选出真正具有近期重大上涨催化剂（如研报调级、技术面突破、重要大合同、行业风口）的 10-12 只标的。

输出格式要求：
请仅返回纯 JSON 格式，不要包含任何 markdown 标记或解释。格式必须为：
{{
    "sectors": ["主线板块1", "主线板块2"],
    "watchlist": ["AAPL", "NVDA", ...],
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
            watchlist = data.get("watchlist", [])
            
            # 7. 纯代码兜底回填：再次强制保证 holdings 里的个股 100% 被纳入标的池，防止 LLM 漏掉
            watchlist = [ticker.strip().upper() for ticker in watchlist if isinstance(ticker, str)]
            for h in holdings:
                if h not in watchlist:
                    watchlist.append(h)
                    
            if len(watchlist) < 5:
                raise ValueError("最终过滤出的个股数量过少。")
                
            logger.info(f"科学选股完成！\n最强行业: {data.get('sectors')}\n逻辑: {data.get('reasoning')}\n最终每日动态监控池(含持仓回填): {watchlist}")
            
            self._save_watchlist(watchlist, data.get("reasoning", ""))
            return watchlist, data.get("reasoning", "基于行业轮动 RS、个股真实 K 线因子及 5 日财报排雷精选。")
            
        except Exception as e:
            fallback_watchlist = list(set(DEFAULT_WATCHLIST + holdings))
            error_msg = f"Alpha Scanner 科学选股异常: {e}，将回退并强制合并持仓。"
            logger.error(f"{error_msg} 最终监控标的池: {fallback_watchlist}")
            self._save_watchlist(fallback_watchlist, "Alpha Scanner failed, fallback to default + holdings")
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

    def _save_watchlist(self, watchlist: list, reason: str):
        today_str = datetime.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d")
        data = {
            "date": today_str,
            "watchlist": watchlist,
            "reason": reason,
            "updated_at": datetime.now(pytz.timezone("US/Eastern")).isoformat()
        }
        
        os.makedirs("data", exist_ok=True)
        with open("data/daily_watchlist.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"今日动态标的池已保存至 data/daily_watchlist.json")
