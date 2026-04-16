"""
配置管理模块
统一管理环境变量和应用配置

v2.0 - 架构改造:
  - 新增固定标的池 WATCHLIST
  - 新增宏观风控评分参数
  - 新增每日复盘配置
"""
import os
from dataclasses import dataclass, field
from typing import Optional

# 尝试加载 .env 文件
def load_dotenv(filepath=".env"):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key not in os.environ:
                        os.environ[key] = value

load_dotenv()


import json
from datetime import datetime
import pytz

# ═══════════════════════════════════════════
# 动态标的池 (Dynamic Watchlist)
# ═══════════════════════════════════════════
DEFAULT_WATCHLIST = [
    "NVDA",   # NVIDIA - AI算力龙头
    "TSM",    # 台积电 - 半导体代工垄断
    "MSFT",   # 微软 - 云+AI双引擎
    "VRT",    # Vertiv - 数据中心电力基础设施
    "CEG",    # Constellation Energy - 核电+AI电力需求
    "LLY",    # 礼来 - GLP-1减肥药龙头
    "ISRG",   # 直觉外科 - 手术机器人垄断
    "SPGI",   # 标普全球 - 信用评级+数据垄断
    "MA",     # 万事达 - 支付网络双寡头
    "GE",     # GE航空 - 航空发动机垄断
]

def load_dynamic_watchlist():
    """尝试加载每日动态生成的标的池，失败则返回默认列表"""
    try:
        watchlist_path = "data/daily_watchlist.json"
        if os.path.exists(watchlist_path):
            with open(watchlist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            # 校验是否为今天的标的池 (美东时间)
            today_str = datetime.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d")
            if data.get("date") == today_str and "watchlist" in data:
                print(f"✅ 成功加载 {today_str} 动态标的池: {data['watchlist']}")
                return data["watchlist"]
    except Exception as e:
        print(f"⚠️ 加载动态标的池失败 ({e})，使用默认列表。")
        
    return DEFAULT_WATCHLIST

WATCHLIST = load_dynamic_watchlist()
WATCHLIST_SYMBOLS = [f"{s}.US" for s in WATCHLIST]

def update_watchlist_in_place(new_watchlist: list):
    """就地更新内存中的标的池，防止模块重载导致的引用脱节"""
    WATCHLIST.clear()
    WATCHLIST.extend(new_watchlist)
    WATCHLIST_SYMBOLS.clear()
    WATCHLIST_SYMBOLS.extend([f"{s}.US" for s in new_watchlist])
    print(f"🔄 内存标的池已就地更新: {WATCHLIST}")


# ═══════════════════════════════════════════
# 宏观风控评分参数
# ═══════════════════════════════════════════
@dataclass
class RiskConfig:
    """
    宏观风控评分配置

    连续评分 0-100:
      0-30:  极端风险，禁止新建仓，考虑减仓
      30-50: 高风险，仅允许减仓或极小仓位
      50-70: 中性，正常交易但降低仓位上限
      70-100: 低风险环境，可正常建仓

    权重分配（总和=100%）:
      market_temperature: 25%  (LongPort市场温度)
      spy_technical:      25%  (SPY技术面)
      rsi_breadth:        15%  (RSI广度)
      capital_flow:       15%  (资金流向)
      sentiment:          10%  (市场情绪)
      volatility:         10%  (波动率)
    """
    # 权重
    weight_market_temp: float = 0.25
    weight_spy_technical: float = 0.25
    weight_rsi_breadth: float = 0.15
    weight_capital_flow: float = 0.15
    weight_sentiment: float = 0.10
    weight_volatility: float = 0.10

    # 阈值
    score_lockdown: int = 30      # 低于此分全面禁止新建仓
    score_cautious: int = 50      # 低于此分进入谨慎模式
    score_normal: int = 70        # 高于此分为正常模式
    score_spy_override: int = 40  # SPY技术面硬约束降级阈值

    # 仓位限制倍率（根据风控评分动态调整）
    # 实际最大仓位 = base_max_position * position_multiplier
    base_max_position_pct: float = 0.30   # 基础单笔最大仓位(占可用现金)
    base_total_position_pct: float = 0.70  # 基础总仓位上限

    @classmethod
    def from_env(cls) -> "RiskConfig":
        return cls(
            score_lockdown=int(os.getenv("RISK_SCORE_LOCKDOWN", "30")),
            score_cautious=int(os.getenv("RISK_SCORE_CAUTIOUS", "50")),
            score_normal=int(os.getenv("RISK_SCORE_NORMAL", "70")),
            score_spy_override=int(os.getenv("RISK_SCORE_SPY_OVERRIDE", "40")),
        )


# ═══════════════════════════════════════════
# 每日复盘配置
# ═══════════════════════════════════════════
@dataclass
class ReviewConfig:
    """每日复盘配置"""
    enabled: bool = True
    trigger_time_hour: int = 16    # 美东时间 16:30 触发
    trigger_time_minute: int = 30
    report_dir: str = "data/logs"  # 复盘报告存储目录

    @classmethod
    def from_env(cls) -> "ReviewConfig":
        return cls(
            enabled=os.getenv("REVIEW_ENABLED", "true").lower() == "true",
            trigger_time_hour=int(os.getenv("REVIEW_HOUR", "16")),
            trigger_time_minute=int(os.getenv("REVIEW_MINUTE", "30")),
        )


# ═══════════════════════════════════════════
# 交易记忆配置
# ═══════════════════════════════════════════
@dataclass
class MemoryConfig:
    """
    交易记忆模块配置

    记忆模块在每日复盘后自动压缩经验教训，
    并在每轮 ReAct 推理前注入历史经验。
    """
    enabled: bool = True
    max_daily_summaries: int = 15  # 保留最近N天的压缩摘要
    max_rules: int = 50            # 最大规则数量
    inject_max_rules: int = 20     # 注入 prompt 时最多展示的规则数
    inject_max_summaries: int = 5  # 注入 prompt 时最多展示的摘要天数

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        return cls(
            enabled=os.getenv("MEMORY_ENABLED", "true").lower() == "true",
            max_daily_summaries=int(os.getenv("MEMORY_MAX_SUMMARIES", "15")),
            max_rules=int(os.getenv("MEMORY_MAX_RULES", "50")),
        )


# ═══════════════════════════════════════════
# 原有配置（保留）
# ═══════════════════════════════════════════

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
    provider: str = "deepseek"
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

    @classmethod
    def from_env_analyst(cls) -> Optional["LLMConfig"]:
        """从环境变量加载分析师LLM配置（如果存在）"""
        provider = os.getenv("ANALYST_LLM_PROVIDER")
        if not provider:
            return None  # 如果未配置，则返回None

        if provider == "deepseek":
            return cls(
                provider="deepseek",
                api_key=os.getenv("ANALYST_DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY", ""),
                base_url=os.getenv("ANALYST_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                model=os.getenv("ANALYST_DEEPSEEK_MODEL", "deepseek-chat"),
            )
        elif provider == "gemini":
            return cls(
                provider="gemini",
                api_key=os.getenv("ANALYST_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY", ""),
                base_url=os.getenv("ANALYST_GEMINI_BASE_URL", ""),
                model=os.getenv("ANALYST_GEMINI_MODEL", "gemini-1.5-flash-latest"),
            )
        elif provider == "openai":
            return cls(
                provider="openai",
                api_key=os.getenv("ANALYST_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
                base_url=os.getenv("ANALYST_OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=os.getenv("ANALYST_OPENAI_MODEL", "gpt-4-turbo"),
            )
        else:
            raise ValueError(f"Unknown Analyst LLM provider: {provider}")


@dataclass
class LogConfig:
    """日志配置"""
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format: str = "%Y-%m-%d %H:%M:%S"
    backup_count: int = 30

    @classmethod
    def from_env(cls) -> "LogConfig":
        return cls(
            log_dir=os.getenv("LOG_DIR", "logs"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


@dataclass
class AgentConfig:
    """智能体配置"""
    max_iterations: int = 10
    sleep_interval_trading: int = 600     # 交易时段休眠间隔（秒）- 改为10分钟
    sleep_interval_non_trading: int = 60  # 非交易时段休眠间隔（秒）

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            max_iterations=int(os.getenv("AGENT_MAX_ITERATIONS", "10")),
            sleep_interval_trading=int(os.getenv("SLEEP_INTERVAL_TRADING", "600")),
            sleep_interval_non_trading=int(os.getenv("SLEEP_INTERVAL_NON_TRADING", "60")),
        )


@dataclass
class FeishuConfig:
    """飞书推送配置"""
    enabled: bool = False
    webhook_url: str = ""

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
    analyst_llm: Optional[LLMConfig] = None  # 分析师专用LLM，可选
    log: LogConfig = field(default_factory=LogConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    @classmethod
    def from_env(cls, llm_provider: str = "deepseek") -> "AppConfig":
        return cls(
            longport=LongPortConfig.from_env(),
            llm=LLMConfig.from_env(llm_provider),
            analyst_llm=LLMConfig.from_env_analyst(),
            log=LogConfig.from_env(),
            agent=AgentConfig.from_env(),
            feishu=FeishuConfig.from_env(),
            risk=RiskConfig.from_env(),
            review=ReviewConfig.from_env(),
            memory=MemoryConfig.from_env(),
        )
