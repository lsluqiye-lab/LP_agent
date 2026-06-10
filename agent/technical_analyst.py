"""
TechnicalAnalyst Agent
"""
import asyncio
import json
from agent.schemas import TechnicalBriefing, TrendStage
from llm.base import BaseLLM, ChatMessage, Role
from tools.market_data import GetTechnicalAnalysisTool

class TechnicalAnalyst:
    """
    The TechnicalAnalyst agent analyzes price action, volume, and technical indicators.
    It uses GetTechnicalAnalysisTool for data and BaseLLM for interpretation.
    """

    def __init__(self, llm: BaseLLM):
        self.llm = llm
        self.tech_tool = GetTechnicalAnalysisTool()

    async def analyze(self, symbol: str) -> TechnicalBriefing:
        print(f"[{self.__class__.__name__}] Performing technical analysis for {symbol}...")

        # Step 1: Get raw technical data
        # Note: BaseTool.execute is synchronous in this project
        raw_data_json = await asyncio.to_thread(self.tech_tool.execute, symbol=symbol)
        raw_data = json.loads(raw_data_json)

        if "error" in raw_data:
            raise ValueError(f"Technical data error: {raw_data['error']}")

        # Step 2: Interpret with LLM
        prompt = self._build_interpretation_prompt(symbol, raw_data)
        
        messages = [
            ChatMessage(role=Role.SYSTEM, content="You are a professional CMT (Chartered Market Technician)."),
            ChatMessage(role=Role.USER, content=prompt)
        ]
        
        print(f"[{self.__class__.__name__}] Interpreting charts and indicators with {self.llm.model}...")
        response = await asyncio.to_thread(self.llm.chat, messages)
        
        if not response.content:
            raise ValueError("LLM returned empty interpretation.")

        # Step 3: Parse and return
        return self._parse_llm_response(response.content)

    def _build_interpretation_prompt(self, symbol: str, data: dict) -> str:
        trend_options = ", ".join(f'"{s}"' for s in TrendStage.__args__)
        
        return f"""
你是一名资深特许市场技术分析师 (CMT)。请对 {symbol} 的技术面进行深度解剖。

## 分析目标：
1. **趋势阶段诊断**: 评估当前所处的趋势周期 (Stage 1 筑底, Stage 2 上升, Stage 3 做头, Stage 4 下降)。
   - **核心约束与松绑**：虽然系统偏好纯正的 Stage 2 (股价 > SMA50 > SMA200)，但**如果**当前处于 Stage 1 末期，股价已强力突破 SMA50，且量价结构(OBV/放量)显示明显的机构吸筹（底部反转/动能爆发），则**允许**将其视为高价值的“早期潜伏买点”，不必死板拘泥于必须站上 SMA200。
2. **量价足迹 (Institutional Footprints)**: 
   - 观察上涨是否放量 (Accumulation)，回调是否缩量 (Dry-up)。
   - 识别是否有 **VCP (波动收缩形态)**。
   - 分析 OBV 和 成交量比率 (Vol Ratio)。
3. **结构位识别**: 寻找当前的“最小阻力线” (Line of Least Resistance) 和关键支撑。

## 技术数据：
{json.dumps(data, indent=2)}

---
## 输出要求 (JSON 格式):
{{
  "trend_stage": {trend_options},
  "summary": "一句话总结当前技术形态 (如：VCP突破前夕、超买回踩中等)",
  "volume_price_analysis": "详细描述量价关系。上涨是否有力？回调是否缩量？是否存在机构派发迹象？",
  "is_volume_breakout": true, // 布尔值：当前是否伴随真实的相对成交量放大 (>1.5x)？如果成交量平淡则必须设为 false。
  "is_rsi_overbought": false, // 布尔值：日线或周线 RSI 是否处于 >75 的危险超买区？
  "key_signals": ["信号1", "信号2"],
  "support_levels": [价格1, 价格2],
  "resistance_levels": [价格1, 价格2],
  "is_pyramid_ready": "boolean, 是否处于理想的加仓位（如缩量回踩均线或即将突破）"
}}
"""

    def _parse_llm_response(self, response_text: str) -> TechnicalBriefing:
        import re
        try:
            # 尝试提取 ```json ... ``` 或 {...} 之间的内容
            match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
            if match:
                json_str = match.group(1)
            else:
                match = re.search(r'(\{.*\})', response_text, re.DOTALL)
                json_str = match.group(1) if match else response_text
            
            return json.loads(json_str.strip())
        except Exception as e:
            print(f"Error parsing Technical JSON: {e}\nRaw: {response_text}")
            raise