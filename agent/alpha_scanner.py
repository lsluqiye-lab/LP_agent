import json
import logging
import os
from datetime import datetime
import pytz

from llm.base import BaseLLM, ChatMessage, Role
from tools.search import get_search_client
from config import DEFAULT_WATCHLIST

logger = logging.getLogger("AlphaScanner")

class AlphaScanner:
    """
    Phase 0: 盘前主线扫描 (Alpha Scanner)
    用于在每天开盘前动态寻找全市场当前最强势的板块和领头羊，更新标的池。
    """
    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.search_client = get_search_client()

    def run(self) -> list:
        logger.info("[Phase 0] 启动 Alpha Scanner 进行盘前动态选股...")
        
        # 1. 搜索当前强势板块与个股
        queries = [
            "US stock market strongest sectors this week leading stocks",
            "IBD 50 top growth stocks breakout today",
            "Wall street upgrades highest conviction buy large cap stocks"
        ]
        
        search_results = ""
        for q in queries:
            try:
                res = self.search_client.search(q)
                search_results += f"\nQuery: {q}\nResults:\n{res}\n"
            except Exception as e:
                logger.error(f"Search failed for {q}: {e}")

        # 2. LLM 分析并生成 Ticker 列表
        system_prompt = """你是一个对冲基金的 Alpha 策略分析师。
你的任务是根据提供的最新市场新闻和分析，找出当前美股市场中最强势的 2-3 个板块，并选出 10-15 只具备极高动能和催化剂的领头羊个股 (Tickers)。

选股原则：
1. 必须是流动性极好的大中盘股 (市值 > 100亿)。
2. 优先选择正处于上升趋势 (Stage 2) 或即将突破的股票。
3. 必须包含少数几只科技巨头 (如 NVDA, MSFT) 作为定海神针。
4. 排除仙股、极高风险的生物科技单药股和流动性差的股票。

输出格式要求：
仅返回纯 JSON 格式数据，不要包含任何 markdown 标记或解释说明。
格式如下：
{
    "sectors": ["Sector 1", "Sector 2"],
    "watchlist": ["AAPL", "NVDA", "TSLA", ...],
    "reasoning": "简短的一句话选股逻辑总结"
}
"""
        messages = [
            ChatMessage(role=Role.SYSTEM, content=system_prompt),
            ChatMessage(role=Role.USER, content=f"这是今天搜索到的市场动态，请帮我生成今天的动态标的池：\n{search_results}")
        ]

        try:
            logger.info("正在请大模型进行板块轮动分析与选股...")
            response = self.llm.chat(messages)
            
            # 清理和解析 JSON
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:-3].strip()
            elif content.startswith("```"):
                content = content[3:-3].strip()
                
            data = json.loads(content)
            watchlist = data.get("watchlist", [])
            
            # 清理 Tickers (转大写，去空格)
            watchlist = [ticker.strip().upper() for ticker in watchlist if isinstance(ticker, str)]
            
            if len(watchlist) < 5:
                raise ValueError("选出的股票过少，使用默认备用列表。")
                
            logger.info(f"选股完成！\n主线板块: {data.get('sectors')}\n逻辑: {data.get('reasoning')}\n入选标的: {watchlist}")
            
            self._save_watchlist(watchlist, data.get("reasoning", ""))
            return watchlist
            
        except Exception as e:
            logger.error(f"Alpha Scanner 选股失败: {e}，将回退到默认列表。")
            self._save_watchlist(DEFAULT_WATCHLIST, "Alpha Scanner failed, fallback to default")
            return DEFAULT_WATCHLIST

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
