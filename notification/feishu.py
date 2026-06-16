"""
飞书消息推送
支持 Webhook 和 App ID/Secret 模式。通过 App ID 可实现图片上传与丰富卡片发送。
同时支持通过 chat_id 直接使用企业自建应用 API 进行独立、安全的卡片和文本投递。
"""
import json
import logging
from typing import Optional

import requests


class FeishuNotifier:
    """
    飞书消息推送器
    """

    def __init__(self, webhook_url: str = "", app_id: str = "", app_secret: str = "", chat_id: str = ""):
        self.webhook_url = webhook_url
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
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
                print(f"获取 tenant_access_token 失败: {data}")
                return None
        except Exception as e:
            print(f"获取 tenant_access_token 异常: {e}")
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
                    print(f"图片上传失败: {result}")
                    return None
        except Exception as e:
            print(f"图片上传异常: {e}")
            return None

    def _send_via_api(self, msg_type: str, content_dict: dict) -> bool:
        """通过自建应用 API 直接向 chat_id 发送消息"""
        token = self._get_tenant_access_token()
        if not token:
            self.logger.error("获取 tenant_access_token 失败，无法通过 API 发送消息。")
            return False

        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        payload = {
            "receive_id": self.chat_id,
            "msg_type": msg_type,
            "content": json.dumps(content_dict)
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                self.logger.info(f"通过 API 发送消息成功: {data.get('data', {}).get('message_id')}")
                return True
            else:
                self.logger.error(f"通过 API 发送消息失败: {data}")
                return False
        except Exception as e:
            self.logger.error(f"通过 API 发送消息异常: {e}")
            return False

    def send_text(self, content: str) -> bool:
        """发送文本消息 (优先通过 Chat ID API，其次 Webhook)"""
        import os
        is_real_dir = "LP_agent_real" in os.getcwd()
        is_dry_run = os.getenv("TRADING_DRY_RUN", "false").lower() == "true"
        env_suffix = " [实盘影子模式]" if is_real_dir and is_dry_run else (" [实盘交易模式]" if is_real_dir else " [模拟测试模式]")
        content = f"{content}\n\n---\n来自: {env_suffix.strip()}"

        if self.chat_id and self.app_id and self.app_secret:
            return self._send_via_api("text", {"text": content})

        if not self.webhook_url:
            print("未配置 Webhook URL 且未配置 Chat ID，无法发送文本消息。")
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
                    print(f"飞书消息发送失败: {data}")
                    return False

            return True

        except Exception as e:
            print(f"飞书消息发送网络异常: {e}")
            return False

    def send_card(self, title: str, content: str, color: str = "blue", footer: str = "LP-Agent v4.3", image_key: Optional[str] = None) -> bool:
        """
        发送飞书卡片消息 (优先通过 Chat ID API，其次 Webhook)
        支持传入 image_key 以在卡片中嵌入图片
        """
        import os
        is_real_dir = "LP_agent_real" in os.getcwd()
        is_dry_run = os.getenv("TRADING_DRY_RUN", "false").lower() == "true"
        env_suffix = " [实盘影子模式]" if is_real_dir and is_dry_run else (" [实盘交易模式]" if is_real_dir else " [模拟测试模式]")
        title = f"{title}{env_suffix}"

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

        card_payload = {
            "header": {
                "title": {
                    "content": title,
                    "tag": "plain_text"
                },
                "template": color
            },
            "elements": elements
        }

        if self.chat_id and self.app_id and self.app_secret:
            return self._send_via_api("interactive", card_payload)

        if not self.webhook_url:
            print("未配置 Webhook URL 且未配置 Chat ID，无法发送卡片消息。")
            return False
            
        try:
            payload = {
                "msg_type": "interactive",
                "card": card_payload
            }
            
            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0 and data.get("StatusCode") != 0:
                print(f"飞书卡片发送失败: {data}")
                return False
            return True
        except Exception as e:
            print(f"飞书卡片发送异常: {e}")
            return False
