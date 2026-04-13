import os
import json
import asyncio
from config import AppConfig, load_dotenv
from llm.gemini import GeminiLLM
from notification.feishu import FeishuNotifier
from agent.react import ReActAgent, STRATEGIC_SYSTEM_PROMPT
from tools.base import ToolRegistry

# 强制加载最新的 .env
load_dotenv("../.env")

async def test_all():
    print("🚀 开始 LP-Agent v3.0 专项测试...")
    
    # 1. 加载配置
    config = AppConfig.from_env("gemini")
    print(f"✅ 配置加载成功: Provider={config.llm.provider}, Model={config.llm.model}")
    print(f"✅ 飞书通知配置: Webhook={config.feishu.webhook_url[:30]}...")

    # 2. 测试 LLM
    llm = GeminiLLM(
        api_key=config.llm.api_key,
        model=config.llm.model
    )
    print(f"⏳ 正在测试 LLM ({config.llm.model})...")
    try:
        from llm.base import ChatMessage, Role
        resp = llm.chat([ChatMessage(role=Role.USER, content="Hello, respond with 'OK'")], tools=None)
        print(f"✅ LLM 响应正常: {resp.content}")
    except Exception as e:
        print(f"❌ LLM 测试失败: {e}")
        return

    # 3. 测试飞书
    notifier = FeishuNotifier(webhook_url=config.feishu.webhook_url)
    print(f"⏳ 正在测试飞书推送...")
    try:
        success = notifier.send_text("🤖 LP-Agent v3.0 自动化测试：模型已更换为 gemini-3-flash-preview，测试消息发送成功。")
        if success:
            print("✅ 飞书推送成功")
        else:
            print("❌ 飞书推送失败")
    except Exception as e:
        print(f"❌ 飞书推送异常: {e}")

    # 4. 测试 ReActAgent 初始化
    print(f"⏳ 正在测试 ReActAgent 初始化...")
    try:
        registry = ToolRegistry()
        agent = ReActAgent(
            llm=llm,
            tool_registry=registry,
            system_prompt=STRATEGIC_SYSTEM_PROMPT,
            feishu_notifier=notifier
        )
        print("✅ ReActAgent 初始化成功")
    except Exception as e:
        print(f"❌ ReActAgent 初始化失败: {e}")

    print("\n✨ 所有关键组件测试完成！")

if __name__ == "__main__":
    asyncio.run(test_all())
