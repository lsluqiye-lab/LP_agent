"""
日志模块
支持按日期轮转的文件日志
"""
import os
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

from config import LogConfig


def setup_logger(
    name: str = "trading_agent",
    config: Optional[LogConfig] = None
) -> logging.Logger:
    """
    设置并返回一个日志记录器

    Args:
        name: 日志记录器名称
        config: 日志配置，如果为None则使用默认配置

    Returns:
        配置好的日志记录器
    """
    if config is None:
        config = LogConfig()

    # 创建日志目录
    if not os.path.exists(config.log_dir):
        os.makedirs(config.log_dir)

    # 获取或创建logger
    logger = logging.getLogger(name)

    # 如果logger已经有handler，说明已经配置过，直接返回
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, config.log_level.upper()))

    # 创建格式化器
    formatter = logging.Formatter(
        fmt=config.log_format,
        datefmt=config.date_format
    )

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, config.log_level.upper()))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件处理器（按日期轮转）
    log_file = os.path.join(config.log_dir, f"{name}.log")
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",  # 每天午夜轮转
        interval=1,
        backupCount=config.backup_count,
        encoding="utf-8"
    )
    file_handler.suffix = "%Y-%m-%d"  # 日志文件后缀格式
    file_handler.setLevel(getattr(logging, config.log_level.upper()))
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


class LoggerMixin:
    """日志混入类，为类提供日志功能"""

    _logger: Optional[logging.Logger] = None

    @property
    def logger(self) -> logging.Logger:
        if self._logger is None:
            self._logger = logging.getLogger(self.__class__.__name__)
        return self._logger

    @logger.setter
    def logger(self, value: logging.Logger):
        self._logger = value


def get_logger(name: str) -> logging.Logger:
    """
    获取一个已配置的logger

    Args:
        name: logger名称

    Returns:
        日志记录器
    """
    return logging.getLogger(name)
