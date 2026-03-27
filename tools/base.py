"""
工具抽象基类
定义工具的通用接口和注册机制
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ToolParameter:
    """工具参数定义"""
    name: str
    type: str  # string, number, integer, boolean, array, object
    description: str
    required: bool = True
    enum: Optional[list] = None
    default: Optional[Any] = None


class BaseTool(ABC):
    """
    工具抽象基类

    所有工具都需要继承此类并实现execute方法
    """

    # 子类需要定义这些属性
    name: str = ""
    description: str = ""
    parameters: list[ToolParameter] = []

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """
        执行工具

        Args:
            **kwargs: 工具参数

        Returns:
            执行结果字符串
        """
        pass

    def to_openai_tool(self) -> dict:
        """
        转换为OpenAI工具调用格式

        Returns:
            OpenAI function calling格式的工具定义
        """
        properties = {}
        required = []

        for param in self.parameters:
            prop = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default

            properties[param.name] = prop

            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            }
        }


class ToolRegistry:
    """
    工具注册表

    管理所有可用工具的注册和查找
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """
        注册工具

        Args:
            tool: 要注册的工具实例
        """
        self._tools[tool.name] = tool

    def register_all(self, tools: list[BaseTool]) -> None:
        """
        批量注册工具

        Args:
            tools: 工具实例列表
        """
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Optional[BaseTool]:
        """
        获取工具

        Args:
            name: 工具名称

        Returns:
            工具实例，如果不存在则返回None
        """
        return self._tools.get(name)

    def get_all(self) -> list[BaseTool]:
        """
        获取所有工具

        Returns:
            所有已注册的工具列表
        """
        return list(self._tools.values())

    def get_openai_tools(self) -> list[dict]:
        """
        获取所有工具的OpenAI格式定义

        Returns:
            OpenAI function calling格式的工具定义列表
        """
        return [tool.to_openai_tool() for tool in self._tools.values()]

    def execute(self, name: str, **kwargs) -> str:
        """
        执行指定工具

        Args:
            name: 工具名称
            **kwargs: 工具参数

        Returns:
            执行结果

        Raises:
            ValueError: 如果工具不存在
        """
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"Tool not found: {name}")
        return tool.execute(**kwargs)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
