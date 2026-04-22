import asyncio
import os
import sys
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)

# 确保能导入根目录下的模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.gemini import GeminiLLM
from llm.base import ChatMessage, Role
from config import AppConfig

async def test_specific_model():
    print("=== 开始测试备用模型: gemini-3-flash-preview ===")
    
    # 获取配置
    config = AppConfig.from_env("gemini")
    
    # 强制指定备用模型进行测试
    llm = GeminiLLM(
        api_key=config.llm.api_key,
        model="gemini-3-flash-preview"
    )
    
    messages = [
        ChatMessage(role=Role.USER, content="你好，请确认你的身份和当前日期。")
    ]
    
    try:
        print("正在发送请求...")
        response = llm.chat(messages)
        print(f"\n模型响应成功！")
        print(f"内容: {response.content}")
    except Exception as e:
        print(f"\n模型调用失败: {e}")

if __name__ == "__main__":
    asyncio.run(test_specific_model())
