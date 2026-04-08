import json
import logging
from typing import Optional

from llm.base import BaseLLM, ChatMessage, Role


ANALYST_SYSTEM_PROMPT = """你是一个顶级的华尔街股票分析师。
你的任务是基于提供给你的单只股票的多维度数据（技术面、基本面、资金面、新闻舆情），
输出一份极度精简、客观的结构化个股评估报告。

你必须严格秉持 Bear-Case-First (风险优先) 的哲学：
1. 先寻找所有可能导致亏损的风险因素。
2. 再评估推动股价上涨的催化剂。

请务必严格按照以下 JSON 格式输出（不要输出任何额外的 markdown 标记或解释文字）：
{
    "symbol": "股票代码",
    "bull_case": "看多理由（一句话总结核心利好）",
    "bear_case": "看空理由/风险点（一句话总结核心利空，如无明显利空填'暂无明确风险'）",
    "score": 1到10的整数 (10分为极度看好，1分为极度看空，5分为中性),
    "recommendation": "BUY" 或 "HOLD" 或 "SELL"
}
"""

class AnalystAgent:
    """
    个股分析师智能体 (Map 阶段)
    负责针对单只股票的原始数据进行深度解析，输出结构化报告
    """

    def __init__(self, llm: BaseLLM, logger: Optional[logging.Logger] = None):
        self.llm = llm
        self.logger = logger or logging.getLogger("AnalystAgent")

    def analyze(self, symbol: str, stock_data: str) -> dict:
        """
        分析单只股票
        """
        self.logger.info(f"开始分析个股: {symbol}")
        
        messages = [
            ChatMessage(role=Role.SYSTEM, content=ANALYST_SYSTEM_PROMPT),
            ChatMessage(role=Role.USER, content=f"目标股票: {symbol}\n\n以下是收集到的所有数据：\n{stock_data}")
        ]

        try:
            response = self.llm.chat(messages, tools=None)
            content = response.content.strip()
            
            # 清理可能的 markdown 标记
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
                
            report = json.loads(content.strip())
            self.logger.info(f"[{symbol}] 分析完成，评分: {report.get('score')}，建议: {report.get('recommendation')}")
            return report
            
        except Exception as e:
            self.logger.error(f"[{symbol}] 分析失败: {e}")
            return {
                "symbol": symbol,
                "bull_case": "分析失败",
                "bear_case": "分析失败",
                "score": 5,
                "recommendation": "HOLD",
                "error": str(e)
            }
