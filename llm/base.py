"""
LLM抽象基类
定义大语言模型的通用接口
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum


class Role(str, Enum):
    """消息角色"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """工具调用信息"""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatMessage:
    """聊天消息"""
    role: Role
    content: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None  # 用于tool角色的消息
    name: Optional[str] = None          # 工具名称（用于tool角色）
    native_content: Optional[Any] = None  # 保存原生LLM对象（如Gemini的Content），避免二次转换丢失字段

    def to_dict(self) -> dict:
        """转换为API调用格式"""
        result = {"role": self.role.value}

        if self.content is not None:
            result["content"] = self.content

        if self.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments if isinstance(tc.arguments, str) else str(tc.arguments)
                    }
                }
                for tc in self.tool_calls
            ]

        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id

        if self.name:
            result["name"] = self.name

        return result


@dataclass
class LLMResponse:
    """LLM响应"""
    content: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None
    finish_reason: str = "stop"
    raw_response: Optional[Any] = None
    native_content: Optional[Any] = None  # 保存原生LLM Content对象，供下一轮消息直接复用

    @property
    def has_tool_calls(self) -> bool:
        """是否包含工具调用"""
        return bool(self.tool_calls)


class BaseLLM(ABC):
    """
    大语言模型抽象基类

    所有LLM实现都需要继承此类并实现chat方法
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        tools: Optional[list[dict]] = None,
        tool_choice: str = "auto"
    ) -> LLMResponse:
        """
        发送聊天请求

        Args:
            messages: 消息列表
            tools: 可用工具列表（OpenAI function calling格式）
            tool_choice: 工具选择策略 ("auto", "none", "required")

        Returns:
            LLM响应
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """获取提供商名称"""
        pass
