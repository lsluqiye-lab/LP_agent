"""
配置管理模块
统一管理环境变量和应用配置
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LongPortConfig:
    """LongPort API配置"""
    app_key: str = ""
    app_secret: str = ""
    access_token: str = ""

    @classmethod
    def from_env(cls) -> "LongPortConfig":
        return cls(
            app_key=os.getenv("LONGPORT_APP_KEY", ""),
            app_secret=os.getenv("LONGPORT_APP_SECRET", ""),
            access_token=os.getenv("LONGPORT_ACCESS_TOKEN", ""),
        )

    def to_env(self):
        """将配置写入环境变量（供LongPort SDK使用）"""
        if self.app_key:
            os.environ["LONGPORT_APP_KEY"] = self.app_key
        if self.app_secret:
            os.environ["LONGPORT_APP_SECRET"] = self.app_secret
        if self.access_token:
            os.environ["LONGPORT_ACCESS_TOKEN"] = self.access_token


@dataclass
class LLMConfig:
    """LLM配置"""
    provider: str = "deepseek"  # deepseek, gemini, openai等
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.7
    max_tokens: int = 4096

    @classmethod
    def from_env(cls, provider: str = "deepseek") -> "LLMConfig":
        if provider == "deepseek":
            return cls(
                provider="deepseek",
                api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            )
        elif provider == "gemini":
            # 注意：主LLM不启用搜索，搜索功能由 tools/search.py 的搜索工具独立实现
            return cls(
                provider="gemini",
                api_key=os.getenv("GEMINI_API_KEY", ""),
                base_url=os.getenv("GEMINI_BASE_URL", ""),
                model=os.getenv("GEMINI_MODEL", "gemini-3-flash-preview"),
            )
        elif provider == "openai":
            return cls(
                provider="openai",
                api_key=os.getenv("OPENAI_API_KEY", ""),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=os.getenv("OPENAI_MODEL", "gpt-4"),
            )
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")


@dataclass
class LogConfig:
    """日志配置"""
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format: str = "%Y-%m-%d %H:%M:%S"
    backup_count: int = 30  # 保留30天的日志

    @classmethod
    def from_env(cls) -> "LogConfig":
        return cls(
            log_dir=os.getenv("LOG_DIR", "logs"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


@dataclass
class AgentConfig:
    """智能体配置"""
    max_iterations: int = 10  # ReAct最大迭代次数
    sleep_interval_trading: int = 300  # 交易时段休眠间隔（秒）
    sleep_interval_non_trading: int = 10  # 非交易时段休眠间隔（秒）

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            max_iterations=int(os.getenv("AGENT_MAX_ITERATIONS", "10")),
            sleep_interval_trading=int(os.getenv("SLEEP_INTERVAL_TRADING", "300")),
            sleep_interval_non_trading=int(os.getenv("SLEEP_INTERVAL_NON_TRADING", "10")),
        )


@dataclass
class FeishuConfig:
    """飞书推送配置"""
    enabled: bool = False  # 是否启用飞书推送
    webhook_url: str = ""  # Webhook URL

    @classmethod
    def from_env(cls) -> "FeishuConfig":
        webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")
        return cls(
            enabled=bool(webhook_url),
            webhook_url=webhook_url,
        )


@dataclass
class AppConfig:
    """应用总配置"""
    longport: LongPortConfig = field(default_factory=LongPortConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    log: LogConfig = field(default_factory=LogConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)

    @classmethod
    def from_env(cls, llm_provider: str = "deepseek") -> "AppConfig":
        return cls(
            longport=LongPortConfig.from_env(),
            llm=LLMConfig.from_env(llm_provider),
            log=LogConfig.from_env(),
            agent=AgentConfig.from_env(),
            feishu=FeishuConfig.from_env(),
        )
