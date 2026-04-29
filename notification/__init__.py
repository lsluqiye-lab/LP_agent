"""
通知模块
提供飞书、钉钉等消息推送能力
"""
from notification.base import NotifierBase
from notification.feishu import FeishuNotifier
from notification.dingtalk import DingTalkNotifier

__all__ = ["NotifierBase", "FeishuNotifier", "DingTalkNotifier"]
