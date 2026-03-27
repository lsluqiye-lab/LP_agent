"""
飞书消息推送
使用飞书群聊 Webhook 机器人发送消息
"""
import json
import logging
from typing import Optional

import requests


class FeishuNotifier:
    """
    飞书 Webhook 消息推送器

    通过飞书群聊自定义机器人的 Webhook URL 发送消息。
    只需要一个 Webhook URL，无需 App ID/Secret。
    """

    def __init__(self, webhook_url: str):
        """
        初始化飞书推送器

        Args:
            webhook_url: 飞书机器人 Webhook URL
        """
        self.webhook_url = webhook_url
        self.logger = logging.getLogger("FeishuNotifier")

    def send_text(self, content: str) -> bool:
        """
        发送文本消息

        Args:
            content: 消息内容

        Returns:
            是否发送成功
        """
        try:
            payload = {
                "msg_type": "text",
                "content": {
                    "text": content
                }
            }
            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            data = resp.json()

            if data.get("code") != 0 and data.get("StatusCode") != 0:
                # Webhook 成功时返回 {"StatusCode":0,"StatusMessage":"success"} 或 {"code":0,"msg":"success"}
                if data.get("StatusMessage") != "success" and data.get("msg") != "success":
                    self.logger.error(f"飞书消息发送失败: {data}")
                    return False

            self.logger.info("飞书消息发送成功")
            return True

        except requests.RequestException as e:
            self.logger.error(f"飞书消息发送网络错误: {e}")
            return False
        except Exception as e:
            self.logger.error(f"飞书消息发送异常: {e}")
            return False

    def send_trading_alert(self, result: str) -> bool:
        """
        发送交易提醒消息

        Args:
            result: 智能体执行结果

        Returns:
            是否发送成功
        """
        message = f"📊 【交易智能体通知】\n\n{result}"
        return self.send_text(message)
