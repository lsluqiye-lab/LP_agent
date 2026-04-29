"""
钉钉消息推送
使用钉钉群聊自定义机器人 Webhook 发送消息
支持加签（HMAC-SHA256）验证方式
"""
import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import requests

from notification.base import NotifierBase


class DingTalkNotifier(NotifierBase):
    """
    钉钉 Webhook 消息推送器

    通过钉钉群聊自定义机器人的 Webhook URL 发送消息。
    支持加签（HMAC-SHA256）验证方式。
    """

    def __init__(self, webhook_url: str, sign_secret: str = ""):
        """
        初始化钉钉推送器

        Args:
            webhook_url: 钉钉机器人 Webhook URL
            sign_secret: 加签密钥（可选，如果设置了加签安全设置）
        """
        self.base_webhook_url = webhook_url
        self.sign_secret = sign_secret
        self.logger = logging.getLogger("DingTalkNotifier")

    def _generate_signed_url(self) -> str:
        """
        生成带签名的 webhook URL

        钉钉加签机制：
        1. 把 timestamp + "\n" + secret 作为签名字符串
        2. 使用 HmacSHA256 算法计算签名
        3. 将签名进行 Base64 encode
        4. 对签名进行 URL encode
        5. 将 timestamp 和 sign 作为 query 参数拼接到 webhook URL

        Returns:
            带签名的 webhook URL
        """
        if not self.sign_secret:
            return self.base_webhook_url

        timestamp = str(round(time.time() * 1000))

        # 计算签名
        string_to_sign = f"{timestamp}\n{self.sign_secret}"
        hmac_code = hmac.new(
            self.sign_secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()

        # Base64 编码并 URL encode
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))

        # 拼接 URL
        separator = "&" if "?" in self.base_webhook_url else "?"
        signed_url = f"{self.base_webhook_url}{separator}timestamp={timestamp}&sign={sign}"

        return signed_url

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
                "msgtype": "text",
                "text": {
                    "content": content
                }
            }

            # 生成带签名的 URL（如果配置了加签）
            webhook_url = self._generate_signed_url()

            resp = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            data = resp.json()

            if data.get("errcode") != 0:
                self.logger.error(f"钉钉消息发送失败: {data}")
                return False

            self.logger.info("钉钉消息发送成功")
            return True

        except requests.RequestException as e:
            self.logger.error(f"钉钉消息发送网络错误: {e}")
            return False
        except Exception as e:
            self.logger.error(f"钉钉消息发送异常: {e}")
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
