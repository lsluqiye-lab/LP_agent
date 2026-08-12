"""
OpenAI LLM实现
"""
import json
import logging
from typing import Optional

from openai import OpenAI
from openai import RateLimitError, APIError, APITimeoutError

from llm.base import BaseLLM, ChatMessage, LLMResponse, ToolCall

logger = logging.getLogger(__name__)

class OpenAILLM(BaseLLM):
    """
    OpenAI (ChatGPT)大语言模型
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
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
        发送聊天请求到OpenAI
        """
        api_messages = [msg.to_dict() for msg in messages]

        kwargs = {
            "model": self.model,
            "messages": api_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            if self.fallback_model:
                logger.warning(f"OpenAI {self.model} 调用失败: {e}，尝试使用降级模型 {self.fallback_model}...")
                kwargs["model"] = self.fallback_model
                response = self.client.chat.completions.create(**kwargs)
            else:
                raise e

        choice = response.choices[0]
        message = choice.message

        tool_calls = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
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
        return "OpenAI"
