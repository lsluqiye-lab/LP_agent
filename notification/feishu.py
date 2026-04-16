"""
飞书消息推送
支持 Webhook 和 App ID/Secret 模式。通过 App ID 可实现图片上传与丰富卡片发送。
"""
import json
import logging
from typing import Optional

import requests


class FeishuNotifier:
    """
    飞书消息推送器
    """

    def __init__(self, webhook_url: str = "", app_id: str = "", app_secret: str = ""):
        self.webhook_url = webhook_url
        self.app_id = app_id
        self.app_secret = app_secret
        self.logger = logging.getLogger("FeishuNotifier")
        self._tenant_access_token = None

    def _get_tenant_access_token(self) -> Optional[str]:
        """获取企业自建应用的 tenant_access_token"""
        if not self.app_id or not self.app_secret:
            return None
        
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                self._tenant_access_token = data.get("tenant_access_token")
                return self._tenant_access_token
            else:
                self.logger.error(f"获取 tenant_access_token 失败: {data}")
                return None
        except Exception as e:
            self.logger.error(f"获取 tenant_access_token 异常: {e}")
            return None

    def upload_image(self, file_path: str) -> Optional[str]:
        """上传图片并获取 image_key"""
        token = self._get_tenant_access_token()
        if not token:
            self.logger.warning("未配置 App ID/Secret 或获取 Token 失败，无法上传图片。")
            return None

        url = "https://open.feishu.cn/open-apis/im/v1/images"
        headers = {
            "Authorization": f"Bearer {token}"
        }
        
        try:
            with open(file_path, "rb") as f:
                files = {"image": f}
                data = {"image_type": "message"}
                resp = requests.post(url, headers=headers, data=data, files=files, timeout=20)
                result = resp.json()
                
                if result.get("code") == 0:
                    image_key = result.get("data", {}).get("image_key")
                    self.logger.info(f"图片上传成功: {image_key}")
                    return image_key
                else:
                    self.logger.error(f"图片上传失败: {result}")
                    return None
        except Exception as e:
            self.logger.error(f"图片上传异常: {e}")
            return None

    def send_text(self, content: str) -> bool:
        """发送文本消息 (优先通过 Webhook)"""
        if not self.webhook_url:
            self.logger.error("未配置 Webhook URL，无法发送文本消息。")
            return False
            
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
                if data.get("StatusMessage") != "success" and data.get("msg") != "success":
                    self.logger.error(f"飞书消息发送失败: {data}")
                    return False

            return True

        except Exception as e:
            self.logger.error(f"飞书消息发送网络异常: {e}")
            return False

    def send_card(self, title: str, content: str, color: str = "blue", footer: str = "LP-Agent v3.0", image_key: Optional[str] = None) -> bool:
        """
        发送飞书卡片消息 (优先通过 Webhook)
        支持传入 image_key 以在卡片中嵌入图片
        """
        if not self.webhook_url:
            self.logger.error("未配置 Webhook URL，无法发送卡片消息。")
            return False
            
        try:
            elements = [
                {
                    "tag": "div",
                    "text": {
                        "content": content,
                        "tag": "lark_md"
                    }
                }
            ]
            
            # 如果提供了 image_key，则添加图片元素
            if image_key:
                elements.append({
                    "tag": "img",
                    "img_key": image_key,
                    "alt": {
                        "tag": "plain_text",
                        "content": "Trade Chart"
                    }
                })
                
            # 添加 Footer
            elements.append({
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": footer
                    }
                ]
            })

            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {
                            "content": title,
                            "tag": "plain_text"
                        },
                        "template": color
                    },
                    "elements": elements
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
                self.logger.error(f"飞书卡片发送失败: {data}")
                return False
            return True
        except Exception as e:
            self.logger.error(f"飞书卡片发送异常: {e}")
            return False
