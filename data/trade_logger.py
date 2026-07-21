"""
交易日志记录模块
基于 JSON 文件存储每日交易记录和智能体决策过程

文件结构:
  data/logs/trade_log_YYYY-MM-DD.json  - 每日交易日志
  data/logs/review_YYYY-MM-DD.json     - 每日复盘报告
  data/logs/risk_score_YYYY-MM-DD.json - 风控评分历史
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

import pytz


logger = logging.getLogger("TradeLogger")

# 日志文件目录
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class TradeLogger:
    """
    交易日志记录器

    将所有交易操作、决策过程、风控评分记录到 JSON 文件中。
    每天一个文件，方便复盘分析。
    """

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = log_dir or LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.eastern = pytz.timezone("US/Eastern")
        self.beijing = pytz.timezone("Asia/Shanghai")

    def _get_today_str(self) -> str:
        """获取美东时间的日期字符串"""
        return datetime.now(self.eastern).strftime("%Y-%m-%d")

    def _get_timestamp(self) -> dict:
        """获取双时区时间戳"""
        now_et = datetime.now(self.eastern)
        now_bj = datetime.now(self.beijing)
        return {
            "eastern": now_et.strftime("%Y-%m-%d %H:%M:%S"),
            "beijing": now_bj.strftime("%Y-%m-%d %H:%M:%S"),
            "unix": int(now_et.timestamp()),
        }

    def _load_daily_log(self, date_str: Optional[str] = None) -> dict:
        """加载当日日志文件，不存在则创建空结构"""
        date_str = date_str or self._get_today_str()
        log_file = self.log_dir / f"trade_log_{date_str}.json"

        if log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"读取日志文件失败: {e}，创建新文件")

        return {
            "date": date_str,
            "created_at": self._get_timestamp(),
            "risk_scores": [],
            "decisions": [],
            "trades": [],
            "errors": [],
            "audit_logs": [],
            "summary": None,
        }

    def _save_daily_log(self, data: dict, date_str: Optional[str] = None):
        """保存当日日志文件"""
        date_str = date_str or self._get_today_str()
        log_file = self.log_dir / f"trade_log_{date_str}.json"

        data["updated_at"] = self._get_timestamp()

        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ═══════════════════════════════════════
    # 风控评分记录
    # ═══════════════════════════════════════

    def log_risk_score(self, score: float, components: dict, regime: str):
        """
        记录一次风控评分

        Args:
            score: 综合风控评分 (0-100)
            components: 各维度得分明细
            regime: 当前风险级别 (lockdown/cautious/normal/favorable)
        """
        daily = self._load_daily_log()
        daily["risk_scores"].append({
            "timestamp": self._get_timestamp(),
            "score": round(score, 1),
            "regime": regime,
            "components": components,
        })
        self._save_daily_log(daily)
        logger.info(f"风控评分记录: {score:.1f} ({regime})")

    # ═══════════════════════════════════════
    # 决策记录
    # ═══════════════════════════════════════

    def log_decision(
        self,
        symbol: str,
        action: str,
        reasoning: dict,
        risk_score: float,
        approved: bool,
    ):
        """
        记录一次交易决策过程

        Args:
            symbol: 股票代码
            action: 决策动作 (BUY/SELL/HOLD)
            reasoning: 推理过程 (包含 bear_case, bull_case, conclusion)
            risk_score: 当时的风控评分
            approved: 是否通过风控审批
        """
        daily = self._load_daily_log()
        daily["decisions"].append({
            "timestamp": self._get_timestamp(),
            "symbol": symbol,
            "action": action,
            "risk_score": round(risk_score, 1),
            "approved": approved,
            "reasoning": reasoning,
        })
        self._save_daily_log(daily)
        logger.info(f"决策记录: {symbol} {action} (approved={approved})")

    # ═══════════════════════════════════════
    # 交易执行记录
    # ═══════════════════════════════════════

    def log_trade(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: Optional[float],
        order_type: str,
        order_id: str,
        reason: str,
        risk_score: float,
        indicators: Optional[dict] = None,
    ):
        """
        记录一次实际交易执行

        Args:
            symbol: 股票代码
            side: Buy/Sell
            quantity: 交易数量
            price: 限价/市价
            order_type: MO/LO
            order_id: 订单ID
            reason: 交易理由
            risk_score: 执行时的风控评分
            indicators: 执行时的技术指标快照
        """
        daily = self._load_daily_log()
        daily["trades"].append({
            "timestamp": self._get_timestamp(),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "order_type": order_type,
            "order_id": order_id,
            "reason": reason,
            "risk_score": round(risk_score, 1),
            "indicators": indicators,
        })
        self._save_daily_log(daily)
        logger.info(f"交易记录: {side} {quantity}股 {symbol} @ {price or '市价'}")

    # ═══════════════════════════════════════
    # 策略审计记录
    # ═══════════════════════════════════════

    def log_audit(self, audit_type: str, symbol: str, event: str, metrics: dict, improvement: Optional[str] = None):
        """
        记录策略审计数据 (打脸率、摩擦成本、策略偏差等)

        Args:
            audit_type: 审计类型 (WHIPSAW, SLIPPAGE, STRATEGY_DEVIATION)
            symbol: 股票代码
            event: 具体事件描述
            metrics: 量化指标
            improvement: 改进建议
        """
        daily = self._load_daily_log()
        daily["audit_logs"].append({
            "timestamp": self._get_timestamp(),
            "audit_type": audit_type,
            "symbol": symbol,
            "event": event,
            "metrics": metrics,
            "improvement": improvement
        })
        self._save_daily_log(daily)
        logger.info(f"策略审计记录: [{audit_type}] {symbol} - {event}")

    # ═══════════════════════════════════════
    # 错误记录
    # ═══════════════════════════════════════

    def log_error(self, context: str, error: str):
        """记录错误"""
        daily = self._load_daily_log()
        daily["errors"].append({
            "timestamp": self._get_timestamp(),
            "context": context,
            "error": error,
        })
        self._save_daily_log(daily)

    # ═══════════════════════════════════════
    # 每日总结
    # ═══════════════════════════════════════

    def save_daily_summary(self, summary: dict):
        """保存每日复盘总结"""
        daily = self._load_daily_log()
        daily["summary"] = {
            "timestamp": self._get_timestamp(),
            **summary,
        }
        self._save_daily_log(daily)
        logger.info("每日复盘总结已保存")

    # ═══════════════════════════════════════
    # 数据查询
    # ═══════════════════════════════════════

    def get_today_log(self) -> dict:
        """获取今日完整日志"""
        return self._load_daily_log()

    def get_date_log(self, date_str: str) -> dict:
        """获取指定日期的日志"""
        return self._load_daily_log(date_str)

    def get_today_trades(self) -> list:
        """获取今日所有交易记录"""
        daily = self._load_daily_log()
        return daily.get("trades", [])

    def get_today_decisions(self) -> list:
        """获取今日所有决策记录"""
        daily = self._load_daily_log()
        return daily.get("decisions", [])

    def get_latest_risk_score(self) -> Optional[dict]:
        """获取最近一次风控评分"""
        daily = self._load_daily_log()
        scores = daily.get("risk_scores", [])
        return scores[-1] if scores else None

    def get_recent_logs(self, days: int = 7) -> list[dict]:
        """
        获取最近N天的日志

        Args:
            days: 天数

        Returns:
            日志列表（按日期倒序）
        """
        from datetime import timedelta

        results = []
        today = datetime.now(self.eastern).date()

        for i in range(days):
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            log_file = self.log_dir / f"trade_log_{date_str}.json"

            if log_file.exists():
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        results.append(data)
                except Exception:
                    continue

        return results

    def get_trade_stats(self, days: int = 30) -> dict:
        """
        获取交易统计（用于复盘）

        Args:
            days: 统计天数

        Returns:
            统计数据
        """
        logs = self.get_recent_logs(days)

        total_trades = 0
        buy_count = 0
        sell_count = 0
        total_decisions = 0
        approved_count = 0
        rejected_count = 0
        risk_scores = []

        for daily in logs:
            trades = daily.get("trades", [])
            decisions = daily.get("decisions", [])
            scores = daily.get("risk_scores", [])

            total_trades += len(trades)
            buy_count += sum(1 for t in trades if t.get("side") == "Buy")
            sell_count += sum(1 for t in trades if t.get("side") == "Sell")

            total_decisions += len(decisions)
            approved_count += sum(1 for d in decisions if d.get("approved"))
            rejected_count += sum(1 for d in decisions if not d.get("approved"))

            risk_scores.extend(s.get("score", 0) for s in scores)

        avg_risk_score = sum(risk_scores) / len(risk_scores) if risk_scores else 0

        return {
            "period_days": days,
            "total_trades": total_trades,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "total_decisions": total_decisions,
            "approved_decisions": approved_count,
            "rejected_decisions": rejected_count,
            "approval_rate_pct": round(
                approved_count / total_decisions * 100, 1
            ) if total_decisions > 0 else 0,
            "avg_risk_score": round(avg_risk_score, 1),
            "risk_score_range": {
                "min": round(min(risk_scores), 1) if risk_scores else 0,
                "max": round(max(risk_scores), 1) if risk_scores else 0,
            },
        }


# 全局单例
_trade_logger: Optional[TradeLogger] = None


def get_trade_logger() -> TradeLogger:
    """获取交易日志记录器单例"""
    global _trade_logger
    if _trade_logger is None:
        _trade_logger = TradeLogger()
    return _trade_logger
