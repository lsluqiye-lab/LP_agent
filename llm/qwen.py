"""
Qwen LLM实现
使用OpenAI兼容API调用Qwen模型 (阿里云百炼 DashScope)
"""
import json
from typing import Optional

from openai import OpenAI

from llm.base import BaseLLM, ChatMessage, LLMResponse, ToolCall


class QwenLLM(BaseLLM):
    """
    Qwen大语言模型

    使用OpenAI兼容的API格式调用阿里云百炼Qwen服务
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-max",
        temperature: float = 0.5,
        max_tokens: int = 4096,
        fallback_model: Optional[str] = None,
    ):
        super().__init__(api_key, base_url, model, temperature, max_tokens, fallback_model)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    def chat(
        self,
        messages: list[ChatMessage],
        tools: Optional[list[dict]] = None,
        tool_choice: str = "auto"
    ) -> LLMResponse:
        """
        发送聊天请求到Qwen

        Args:
            messages: 消息列表
            tools: 可用工具列表（OpenAI function calling格式）
            tool_choice: 工具选择策略

        Returns:
            LLM响应
        """
        # 转换消息格式
        api_messages = [msg.to_dict() for msg in messages]

        # 构建请求参数
        kwargs = {
            "model": self.model,
            "messages": api_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        # 如果有工具，添加工具参数
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        # 调用API
        response = self.client.chat.completions.create(**kwargs)

        # 解析响应
        choice = response.choices[0]
        message = choice.message

        # 解析工具调用
        tool_calls = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                # 解析参数JSON
                try:
                    arguments = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    arguments = {"raw": tc.function.arguments}

                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments
                ))

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            raw_response=response
        )

    def get_provider_name(self) -> str:
        return "Qwen"
