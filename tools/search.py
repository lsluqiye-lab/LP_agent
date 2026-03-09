"""
搜索工具集
使用 Gemini + Google Search 提供实时信息搜索能力
这些工具供 ReAct 智能体按需调用，避免每次 LLM 调用都开启搜索
"""
import json
import os
import logging
import time
from typing import Optional, ClassVar

from google import genai
from google.genai import types

from tools.base import BaseTool, ToolParameter


class GeminiSearchClient:
    """
    Gemini 搜索客户端（单例）
    内部启用 Google Search，专门用于搜索工具
    """
    _instance: Optional["GeminiSearchClient"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY 环境变量未设置，搜索工具不可用")

        self.client = genai.Client(api_key=api_key)
        self.model = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
        self.logger = logging.getLogger("GeminiSearch")
        self._initialized = True

    def search(self, query: str, system_prompt: str = "") -> str:
        """
        执行搜索查询

        Args:
            query: 搜索查询内容
            system_prompt: 系统提示词，指导搜索结果的格式和重点

        Returns:
            搜索结果文本
        """
        try:
            config_kwargs = {
                "temperature": 0.3,  # 搜索场景用较低温度，更精确
                "max_output_tokens": 4096,
                # 启用 Google Search
                "tools": [types.Tool(google_search=types.GoogleSearch())],
            }

            if system_prompt:
                config_kwargs["system_instruction"] = system_prompt

            config = types.GenerateContentConfig(**config_kwargs)

            response = self.client.models.generate_content(
                model=self.model,
                contents=[types.Content(
                    role="user",
                    parts=[types.Part(text=query)]
                )],
                config=config,
            )

            # 提取文本结果
            result_text = ""
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.text:
                        result_text += part.text

            return result_text or "未找到相关信息"

        except Exception as e:
            self.logger.error(f"Gemini搜索出错: {e}")
            return f"搜索出错: {str(e)}"


def get_search_client() -> GeminiSearchClient:
    """获取搜索客户端单例"""
    return GeminiSearchClient()


class CachedSearchTool(BaseTool):
    """
    带缓存的搜索工具基类

    短期内结果不变的搜索（如宏观经济、财报日历、地缘政治）适合使用此基类。
    缓存 key = 工具名 + 调用参数，TTL 内重复调用直接返回上次结果。
    """
    cache_ttl: int = 3600  # 默认缓存1小时，子类可覆盖
    _cache: ClassVar[dict] = {}  # 全局缓存，所有实例共享，key 包含工具名防冲突

    def execute(self, **kwargs) -> str:
        cache_key = json.dumps(
            {"tool": self.name, **kwargs}, sort_keys=True, ensure_ascii=False
        )
        if cache_key in self._cache:
            cached_time, cached_result = self._cache[cache_key]
            remaining = self.cache_ttl - (time.time() - cached_time)
            if remaining > 0:
                logging.getLogger(self.__class__.__name__).info(
                    f"缓存命中 [{self.name}]，剩余有效期 {int(remaining)} 秒"
                )
                return cached_result

        result = self._search(**kwargs)
        self._cache[cache_key] = (time.time(), result)
        return result

    def _search(self, **kwargs) -> str:
        """子类实现具体搜索逻辑"""
        raise NotImplementedError


class SearchStockNewsTool(BaseTool):
    """搜索股票新闻工具"""

    name = "search_stock_news"
    description = "搜索指定股票或指数的最新新闻、公告、重大事件。适用于了解个股或市场的最新动态。"
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码或指数名称，如 AAPL、TSLA、SPY、纳斯达克 等"
        ),
        ToolParameter(
            name="focus",
            type="string",
            description="关注重点：earnings(财报)、merger(并购)、product(产品)、general(综合)",
            required=False,
            default="general",
            enum=["earnings", "merger", "product", "general"]
        )
    ]

    SYSTEM_PROMPT = """你是一个专业的金融新闻分析师。
请搜索并总结目标股票/指数的最新新闻，输出格式如下：

## 最新动态
- 列出3-5条最重要的新闻，每条包含：日期、标题、简要内容

## 市场影响
- 简要分析这些新闻对股价可能的影响（利好/利空/中性）

## 关键信息
- 提取关键数据点（如财报数据、目标价、评级变化等）

请用中文回复，保持简洁专业。"""

    def execute(self, symbol: str, focus: str = "general", **kwargs) -> str:
        try:
            client = get_search_client()

            focus_map = {
                "earnings": "财报 业绩 收入 利润",
                "merger": "并购 收购 合并 重组",
                "product": "产品 发布 创新 技术",
                "general": "新闻 动态 公告"
            }
            focus_keywords = focus_map.get(focus, "新闻")

            query = f"{symbol} 股票 {focus_keywords} 最新消息 site:finance.yahoo.com OR site:reuters.com OR site:bloomberg.com"

            result = client.search(query, self.SYSTEM_PROMPT)

            return json.dumps({
                "symbol": symbol,
                "focus": focus,
                "news": result
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class SearchMarketSentimentTool(BaseTool):
    """搜索市场舆情工具"""

    name = "search_market_sentiment"
    description = "搜索社交媒体（Twitter/X、Reddit、StockTwits等）上关于股票的讨论和舆情，了解散户情绪。"
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码，如 AAPL、TSLA、GME 等"
        ),
        ToolParameter(
            name="platform",
            type="string",
            description="社交平台：reddit、twitter、all(全部)",
            required=False,
            default="all",
            enum=["reddit", "twitter", "all"]
        )
    ]

    SYSTEM_PROMPT = """你是一个社交媒体舆情分析专家。
请分析目标股票在社交媒体上的讨论热度和情绪，输出格式如下：

## 舆情概览
- 讨论热度：高/中/低
- 整体情绪：看多/看空/中性/分歧明显

## 热门观点
- 列出3-5个有代表性的观点（标注来源平台）

## 关键话题
- 当前散户关注的主要话题是什么

## 风险提示
- 是否有异常炒作迹象或需要警惕的信号

请用中文回复，区分事实与观点。"""

    def execute(self, symbol: str, platform: str = "all", **kwargs) -> str:
        try:
            client = get_search_client()

            platform_sites = {
                "reddit": "site:reddit.com/r/wallstreetbets OR site:reddit.com/r/stocks",
                "twitter": "site:twitter.com OR site:x.com",
                "all": "site:reddit.com OR site:twitter.com OR site:stocktwits.com"
            }
            site_filter = platform_sites.get(platform, platform_sites["all"])

            query = f"${symbol} stock {site_filter}"

            result = client.search(query, self.SYSTEM_PROMPT)

            return json.dumps({
                "symbol": symbol,
                "platform": platform,
                "sentiment": result
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class SearchFinancialAnalysisTool(BaseTool):
    """搜索财务分析工具"""

    name = "search_financial_analysis"
    description = "搜索分析师评级、目标价、财务指标、机构持仓等专业分析信息。"
    parameters = [
        ToolParameter(
            name="symbol",
            type="string",
            description="股票代码，如 AAPL、MSFT 等"
        ),
        ToolParameter(
            name="analysis_type",
            type="string",
            description="分析类型：rating(评级)、target(目标价)、institutional(机构持仓)、valuation(估值)",
            required=False,
            default="rating",
            enum=["rating", "target", "institutional", "valuation"]
        )
    ]

    SYSTEM_PROMPT = """你是一个专业的股票研究分析师。
请搜索并汇总目标股票的专业分析信息，输出格式如下：

## 分析师观点
- 最新评级：买入/持有/卖出（汇总多家机构）
- 目标价区间：$XX - $XX
- 近期评级变化

## 关键指标
- P/E、P/S、PEG 等估值指标
- 营收增长率、利润率等

## 机构动向
- 近期机构买入/卖出情况
- 重要持仓变化

请用中文回复，数据尽量准确，标注数据来源和时间。"""

    def execute(self, symbol: str, analysis_type: str = "rating", **kwargs) -> str:
        try:
            client = get_search_client()

            type_keywords = {
                "rating": "analyst rating upgrade downgrade",
                "target": "price target analyst",
                "institutional": "institutional holdings 13F filing",
                "valuation": "valuation PE ratio PEG"
            }
            keywords = type_keywords.get(analysis_type, "analyst rating")

            query = f"{symbol} stock {keywords} site:tipranks.com OR site:seekingalpha.com OR site:finviz.com"

            result = client.search(query, self.SYSTEM_PROMPT)

            return json.dumps({
                "symbol": symbol,
                "analysis_type": analysis_type,
                "analysis": result
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class SearchMacroEconomicsTool(CachedSearchTool):
    """搜索宏观经济信息工具（带1小时缓存）"""

    name = "search_macro_economics"
    description = "搜索美联储政策、利率、通胀、就业等宏观经济信息，了解大盘环境。"
    parameters = [
        ToolParameter(
            name="topic",
            type="string",
            description="关注主题：fed(美联储)、inflation(通胀)、employment(就业)、gdp(经济增长)、all(综合)",
            required=False,
            default="all",
            enum=["fed", "inflation", "employment", "gdp", "all"]
        )
    ]

    SYSTEM_PROMPT = """你是一个宏观经济分析师。
请搜索并总结当前美国宏观经济形势，输出格式如下：

## 最新动态
- 近期重要经济数据发布或政策变化

## 美联储政策
- 当前利率水平
- 下次会议预期（加息/降息/维持）
- 官员最新表态

## 市场影响
- 对股市的潜在影响分析
- 需要关注的风险点

## 关键日期
- 近期重要经济数据发布日期

请用中文回复，关注最新信息。"""

    def _search(self, topic: str = "all", **kwargs) -> str:
        try:
            client = get_search_client()

            topic_queries = {
                "fed": "Federal Reserve interest rate decision FOMC",
                "inflation": "US inflation CPI PPI latest",
                "employment": "US jobs report unemployment NFP",
                "gdp": "US GDP growth economic outlook",
                "all": "US economy Fed interest rate inflation latest"
            }
            query = topic_queries.get(topic, topic_queries["all"])
            query += " site:federalreserve.gov OR site:bls.gov OR site:reuters.com"

            result = client.search(query, self.SYSTEM_PROMPT)

            return json.dumps({
                "topic": topic,
                "macro_info": result
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class SearchEarningsCalendarTool(CachedSearchTool):
    """搜索财报日历工具（带2小时缓存）"""

    name = "search_earnings_calendar"
    description = "搜索近期将要发布财报的重要公司，以及已发布财报的结果。"
    cache_ttl: int = 3600 * 2
    parameters = [
        ToolParameter(
            name="time_range",
            type="string",
            description="时间范围：this_week(本周)、next_week(下周)、recent(近期发布)",
            required=False,
            default="this_week",
            enum=["this_week", "next_week", "recent"]
        ),
        ToolParameter(
            name="symbol",
            type="string",
            description="特定股票代码（可选），如果指定则搜索该股票的财报信息",
            required=False
        )
    ]

    SYSTEM_PROMPT = """你是一个财报日历追踪专家。
请搜索财报日程和结果，输出格式如下：

## 即将发布
- 列出重要公司的财报发布日期和预期

## 近期已发布
- 列出已发布财报的公司，包含：
  - EPS（实际 vs 预期）
  - 营收（实际 vs 预期）
  - 业绩指引

## 市场关注
- 本周最受关注的财报有哪些

请用中文回复，关注大型科技股和热门股票。"""

    def _search(self, time_range: str = "this_week", symbol: str = None, **kwargs) -> str:
        try:
            client = get_search_client()

            if symbol:
                query = f"{symbol} earnings report date EPS revenue"
            else:
                time_map = {
                    "this_week": "this week",
                    "next_week": "next week",
                    "recent": "latest"
                }
                time_phrase = time_map.get(time_range, "this week")
                query = f"earnings calendar {time_phrase} site:earningswhispers.com OR site:finance.yahoo.com"

            result = client.search(query, self.SYSTEM_PROMPT)

            return json.dumps({
                "time_range": time_range,
                "symbol": symbol,
                "earnings_info": result
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


class SearchGeopoliticalNewsTool(CachedSearchTool):
    """搜索地缘政治与国内政治新闻工具（带1小时缓存）"""

    name = "search_geopolitical_news"
    description = "搜索可能影响股市的重大地缘政治事件和美国国内政治新闻，如战争冲突、贸易摩擦、关税政策、制裁等。"
    parameters = [
        ToolParameter(
            name="region",
            type="string",
            description="关注地区/主题：middle_east(中东局势)、us_china(中美关系)、us_domestic(美国国内政治)、trade(贸易/关税)、all(综合)",
            required=False,
            default="all",
            enum=["middle_east", "us_china", "us_domestic", "trade", "all"]
        )
    ]

    SYSTEM_PROMPT = """你是一个专注于地缘政治与金融市场关联分析的专家。
请搜索近期重大政治事件，并评估其对美股市场的潜在影响，输出格式如下：

## 重大事件
- 列出3-5条近期最重要的政治/地缘事件，每条包含：日期、事件摘要

## 市场影响分析
- 对能源/大宗商品的影响（如战争、海峡封锁等）
- 对科技股的影响（如制裁、出口管制等）
- 对整体市场风险偏好的影响（避险情绪升/降）

## 需要关注的风险
- 列出当前最值得警惕的政治风险点

请用中文回复，聚焦可能引发股市大幅波动的事件。"""

    def _search(self, region: str = "all", **kwargs) -> str:
        try:
            client = get_search_client()

            region_queries = {
                "middle_east": "Middle East conflict war oil strait Hormuz Iran Israel latest",
                "us_china": "US China trade war tariffs sanctions technology ban latest",
                "us_domestic": "US politics congress White House policy market impact latest",
                "trade": "tariffs trade war sanctions import export policy latest",
                "all": "geopolitical risk war conflict tariffs sanctions market impact latest",
            }
            query = region_queries.get(region, region_queries["all"])
            query += " site:reuters.com OR site:bloomberg.com OR site:wsj.com OR site:ft.com"

            result = client.search(query, self.SYSTEM_PROMPT)

            return json.dumps({
                "region": region,
                "geopolitical_news": result
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)


def create_search_tools() -> list[BaseTool]:
    """
    创建所有搜索工具实例

    Returns:
        搜索工具列表
    """
    return [
        SearchStockNewsTool(),
        SearchMarketSentimentTool(),
        SearchFinancialAnalysisTool(),
        SearchMacroEconomicsTool(),
        SearchEarningsCalendarTool(),
        SearchGeopoliticalNewsTool(),
    ]
