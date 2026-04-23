"""
Gemini LLM实现
使用 Google 官方 google-genai SDK 调用 Gemini 模型
支持 Gemini 内置的 Google Search 工具
"""
import json
import uuid
import logging
from typing import Optional

from google import genai
from google.genai import types

from llm.base import BaseLLM, ChatMessage, Role, LLMResponse, ToolCall

logger = logging.getLogger("gemini_llm")


class GeminiLLM(BaseLLM):
    """
    Google Gemini大语言模型

    使用 google-genai 官方 SDK，支持 Gemini 原生能力（如 Google Search）
    安装依赖: pip install google-genai
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "",  # 官方SDK不需要base_url，保留此参数保持接口一致
        model: str = "gemini-3.1-pro-preview",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        fallback_model: Optional[str] = None,
    ):
        super().__init__(api_key, base_url, model, temperature, max_tokens, fallback_model)
        self.client = genai.Client(api_key=api_key)

    def _convert_tools(self, openai_tools: list[dict]) -> list:
        """
        将 OpenAI function calling 格式的工具定义转换为 Gemini FunctionDeclaration 格式
        """
        # 类型映射：OpenAI类型 → Gemini Schema类型
        type_map = {
            "string": "STRING",
            "integer": "INTEGER",
            "number": "NUMBER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }

        function_declarations = []
        for tool in openai_tools:
            func = tool["function"]
            params = func.get("parameters", {})

            # 构建属性定义
            properties = {}
            for prop_name, prop_def in params.get("properties", {}).items():
                raw_type = prop_def.get("type", "string")
                gemini_type = type_map.get(raw_type, "STRING")
                prop_schema = types.Schema(
                    type=gemini_type,
                    description=prop_def.get("description", ""),
                )
                if prop_def.get("enum"):
                    prop_schema = types.Schema(
                        type=gemini_type,
                        description=prop_def.get("description", ""),
                        enum=prop_def["enum"],
                    )
                properties[prop_name] = prop_schema

            schema = types.Schema(
                type="OBJECT",
                properties=properties,
                required=params.get("required", []),
            )

            function_declarations.append(types.FunctionDeclaration(
                name=func["name"],
                description=func.get("description", ""),
                parameters=schema,
            ))

        gemini_tools = [types.Tool(function_declarations=function_declarations)]

        return gemini_tools

    def _convert_messages(self, messages: list[ChatMessage]) -> tuple[Optional[str], list]:
        """
        将 ChatMessage 列表转换为 Gemini Contents 格式

        Returns:
            (system_instruction, contents) 元组
            Gemini 的 system prompt 需要单独传入，不能放在 contents 里
        """
        system_instruction = None
        contents = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_instruction = msg.content
                continue

            # 如果消息保存了原生 Gemini Content 对象，直接使用
            # 这是为了保留 thought_signature 等 Gemini 内部字段，避免重建时丢失
            if msg.native_content is not None:
                contents.append(msg.native_content)
                continue

            if msg.role == Role.USER:
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text=msg.content or "")]
                ))

            elif msg.role == Role.ASSISTANT:
                parts = []
                if msg.content:
                    parts.append(types.Part(text=msg.content))
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        parts.append(types.Part(
                            function_call=types.FunctionCall(
                                name=tc.name,
                                args=tc.arguments,
                            )
                        ))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))

            elif msg.role == Role.TOOL:
                # 工具结果：在 Gemini 中放在 user 角色里
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(
                        function_response=types.FunctionResponse(
                            name=msg.name or "unknown_tool",
                            response={"result": msg.content or ""},
                        )
                    )]
                ))

        return system_instruction, contents

    def chat(
        self,
        messages: list[ChatMessage],
        tools: Optional[list[dict]] = None,
        tool_choice: str = "auto"
    ) -> LLMResponse:
        """
        发送聊天请求到 Gemini (支持自动降级)

        Args:
            messages: 消息列表
            tools: 可用工具列表（OpenAI function calling格式，会自动转换）
            tool_choice: 暂不生效，Gemini 统一使用 AUTO 模式

        Returns:
            LLM响应
        """
        try:
            return self._execute_chat(self.model, messages, tools, tool_choice)
        except Exception as e:
            if self.fallback_model:
                logger.warning(f"Gemini 主模型 {self.model} 调用失败: {e}。正在尝试降级到备用模型 {self.fallback_model}...")
                try:
                    return self._execute_chat(self.fallback_model, messages, tools, tool_choice)
                except Exception as fe:
                    logger.error(f"Gemini 备用模型 {self.fallback_model} 调用也失败: {fe}")
                    raise fe
            else:
                logger.error(f"Gemini 模型 {self.model} 调用失败且未配置备用模型: {e}")
                raise e

    def _execute_chat(
        self,
        model_name: str,
        messages: list[ChatMessage],
        tools: Optional[list[dict]] = None,
        tool_choice: str = "auto"
    ) -> LLMResponse:
        """
        执行具体的聊天请求逻辑
        """
        system_instruction, contents = self._convert_messages(messages)

        # 构建 GenerateContentConfig
        config_kwargs = {
            "temperature": self.temperature,
            "max_output_tokens": self.max_tokens,
            "http_options": types.HttpOptions(timeout=120.0)
        }

        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        if tools:
            config_kwargs["tools"] = self._convert_tools(tools)
            config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
                disable=True  # 由 ReAct 框架手动处理工具调用
            )

        config = types.GenerateContentConfig(**config_kwargs)

        response = self.client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )

        # 解析响应
        candidate = response.candidates[0]
        text_content = None
        tool_calls = None

        for part in candidate.content.parts:
            if part.text:
                text_content = (text_content or "") + part.text
            if part.function_call:
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(ToolCall(
                    id=f"call_{part.function_call.name}_{uuid.uuid4().hex[:8]}",
                    name=part.function_call.name,
                    arguments=dict(part.function_call.args),
                ))

        finish_reason = str(candidate.finish_reason) if candidate.finish_reason else "stop"

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw_response=response,
            # 保存原始 Content 对象，下一轮复用时可直接传给 Gemini
            # 避免重新构建 function_call parts 时丢失 thought_signature 字段
            native_content=candidate.content,
        )

    def get_provider_name(self) -> str:
        return "Google Gemini"
