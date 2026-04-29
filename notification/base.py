"""
通知模块抽象基类
定义统一的通知接口，支持多种推送渠道
"""
from abc import ABC, abstractmethod


class NotifierBase(ABC):
    """
    通知器抽象基类
    
    所有通知渠道（飞书、钉钉等）都需要继承此基类，
    实现统一的发送接口。
    """

    @abstractmethod
    def send_text(self, content: str) -> bool:
        """
        发送文本消息

        Args:
            content: 消息内容

        Returns:
            是否发送成功
        """
        pass

    @abstractmethod
    def send_trading_alert(self, result: str) -> bool:
        """
        发送交易提醒消息

        Args:
            result: 智能体执行结果

        Returns:
            是否发送成功
        """
        pass
