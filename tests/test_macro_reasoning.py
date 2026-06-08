
import unittest
import asyncio
import json
from unittest.mock import MagicMock
from agent.react import ReActAgent, STRATEGIC_SYSTEM_PROMPT
from llm.base import BaseLLM, ChatMessage, Role, LLMResponse

class TestMacroDeepReasoning(unittest.TestCase):
    def setUp(self):
        self.mock_llm = MagicMock(spec=BaseLLM)
        self.mock_tool_registry = MagicMock()
        self.agent = ReActAgent(self.mock_llm, self.mock_tool_registry)

    def test_structural_employment_reasoning(self):
        """
        测试 CIO 是否能识别非农数据中的结构性风险：
        输入：非农总量利好（增加30万），但细节显示服务业增加、高科技裁员。
        预期：CIO 的 Thought 中应出现对高科技行业（如 NVDA, TSM）的谨慎态度，而不仅仅是看到总量增加就看多。
        """
        # 模拟专家简报
        briefings = json.dumps({
            "SentimentAnalyst": "Non-farm payrolls increased by 300k, beating expectations. However, deeper analysis shows growth is entirely in low-wage services, while high-tech and finance sectors saw 50k job cuts. Fed remains hawkish on persistent service inflation.",
            "TechnicalAnalyst": "NVDA is at ATH, RSI is 72. Bullish momentum."
        })
        
        # 模拟 LLM 的响应
        # 我们希望在 Thought 中看到类似 "structural contradiction", "tech layoffs", "caution on NVDA despite total NFP growth" 等词汇
        mock_response = LLMResponse(
            content="""【逻辑心流】
我注意到非农数据虽然总量增加 30 万，但结构上存在严重矛盾：低端服务业膨胀而高科技行业（我们核心仓位所在的领域）正在大规模裁员。这暗示了核心购买力和行业增长动力正在萎缩。同时，服务业的通胀压力可能迫使美联储维持高利率，这对于 NVDA 这种高估值、高 Beta 的科技股是巨大的压制。即使技术面仍然看涨，但在这种宏观结构性走弱的情况下，我决定保持谨慎，不进行任何加仓动作。

【最终指令】
- Symbol: NVDA
- Action: HOLD
- Reason: 宏观结构利空高科技行业。""",
            tool_calls=[]
        )
        self.mock_llm.chat.return_value = mock_response

        # 运行 Agent (模拟异步环境)
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(self.agent.run(briefings, risk_score=85))

        print(f"Agent reasoning output:\n{result}")
        
        # 验证推理内核
        self.assertIn("结构", result)
        self.assertIn("裁员", result)
        self.assertIn("服务业", result)
        self.assertIn("NVDA", result)
        self.assertTrue("HOLD" in result or "SELL" in result or "TIGHTEN" in result, "由于结构性风险，不应盲目 BUY")

if __name__ == '__main__':
    unittest.main()
