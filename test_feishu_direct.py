from config import AppConfig
from notification.feishu import FeishuNotifier

config = AppConfig.from_env()
notifier = FeishuNotifier(
    webhook_url=config.feishu.webhook_url,
    app_id=config.feishu.app_id,
    app_secret=config.feishu.app_secret
)

print(f"Enabled: {config.feishu.enabled}")
print(f"Webhook: {config.feishu.webhook_url}")

success = notifier.send_text("这是一条测试消息 from LP-Agent")
print(f"Text sent: {success}")

success_card = notifier.send_card("测试卡片", "这是一个测试卡片消息")
print(f"Card sent: {success_card}")
