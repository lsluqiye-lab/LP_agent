"""
每日复盘 Agent
收盘后自动执行，总结当日操作、分析胜败因素、生成改进建议

触发时机: 美东时间 16:30（可配置）
输出: JSON 复盘报告 + 飞书推送摘要
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

import pytz

from llm.base import BaseLLM, ChatMessage, Role
from data.trade_logger import get_trade_logger
from data.memory import TradingMemory, get_trading_memory
from config import WATCHLIST, ReviewConfig
from notification.feishu import FeishuNotifier


logger = logging.getLogger("ReviewAgent")


REVIEW_SYSTEM_PROMPT = """你是一个专业的交易复盘分析师。你的任务是对AI交易智能体的当日表现进行客观、结构化的复盘。

## 复盘原则
1. **客观记录**：如实记录今日所有决策和交易，不美化不回避
2. **因果分析**：每笔交易/每个决策，分析"为什么做了这个决定"以及"结果如何"
3. **Bear-Case反思**：检查今天是否有被忽略的风险信号
4. **可操作建议**：给出具体的、可以在明天执行的改进建议

## 输出格式（严格按此结构）

### 一、今日总览
- 日期 / 交易日状态
- 风控评分范围 (最低~最高) 及平均值
- 总决策次数 / 通过审批 / 被拒绝
- 总交易次数 (买入X笔 / 卖出X笔)

### 二、持仓变动
- 新建仓：标的、数量、价格、理由
- 卖出：标的、数量、价格、理由、盈亏
- 维持持有：标的、当前盈亏、原因

### 三、决策质量评估
对每个重要决策：
- 决策内容
- Bear Case 是否充分？是否遗漏了什么？
- 事后看，这个决策是否正确？
- 如果重来，应该怎么做？

### 四、风控系统评估
- 风控评分是否准确反映了市场状态？
- 是否有应该阻止但放行的交易？
- 是否有不应该阻止但被拒绝的机会？

### 五、明日关注
- 需要重点监控的持仓（接近止损/止盈线）
- 需要关注的标的池机会
- 需要关注的宏观事件/数据

### 六、改进建议
- 具体的、可执行的建议（1-3条）
- 参数调整建议（如果有的话）

请用中文回复，保持简洁专业。
"""


class ReviewAgent:
    """
    每日复盘智能体

    收集当日交易日志，调用 LLM 进行结构化复盘分析，
    生成报告并通过飞书推送。
    """

    def __init__(
        self,
        llm: BaseLLM,
        config: Optional[ReviewConfig] = None,
        feishu_notifier: Optional[FeishuNotifier] = None,
        trading_memory: Optional[TradingMemory] = None,
        logger_instance: Optional[logging.Logger] = None,
    ):
        self.llm = llm
        self.config = config or ReviewConfig()
        self.feishu_notifier = feishu_notifier
        self.trading_memory = trading_memory or get_trading_memory()
        self.log = logger_instance or logger
        self.trade_logger = get_trade_logger()
        self.eastern = pytz.timezone("US/Eastern")
        self._today_reviewed = False

    def should_run(self) -> bool:
        """
        检查是否应该执行复盘

        Returns:
            True if 当前时间已过触发时间且今天还没复盘
        """
        if not self.config.enabled:
            return False

        if self._today_reviewed:
            return False

        now = datetime.now(self.eastern)
        trigger_hour = self.config.trigger_time_hour
        trigger_minute = self.config.trigger_time_minute

        current_minutes = now.hour * 60 + now.minute
        trigger_minutes = trigger_hour * 60 + trigger_minute

        # 过了触发时间但还没复盘
        return current_minutes >= trigger_minutes

    def run(self) -> str:
        """
        执行每日复盘

        Returns:
            复盘报告文本
        """
        self.log.info("=" * 50)
        self.log.info("开始每日复盘")

        try:
            # 收集当日数据
            today_log = self.trade_logger.get_today_log()
            recent_stats = self.trade_logger.get_trade_stats(days=7)

            # 构建 LLM 输入
            review_data = self._prepare_review_data(today_log, recent_stats)

            messages = [
                ChatMessage(role=Role.SYSTEM, content=REVIEW_SYSTEM_PROMPT),
                ChatMessage(role=Role.USER, content=review_data),
            ]

            # 调用 LLM
            response = self.llm.chat(messages, tools=None)
            report = response.content or "复盘报告生成失败"

            # 保存报告
            self._save_report(report, today_log)

            # 压缩经验写入记忆模块
            self._update_memory(report, today_log)

            # 推送飞书
            self._push_to_feishu(report)

            self._today_reviewed = True
            self.log.info("每日复盘完成")

            return report

        except Exception as e:
            self.log.error(f"复盘执行出错: {e}", exc_info=True)
            # 即使出错也标记为已复盘，防止在主循环中由于 retry 导致死循环（尤其是 API 配额耗尽时）
            self._today_reviewed = True
            return f"复盘出错: {str(e)}"

    def reset_daily_flag(self):
        """重置每日复盘标志（新的一天开始时调用）"""
        self._today_reviewed = False

    def _prepare_review_data(self, today_log: dict, recent_stats: dict) -> str:
        """
        准备复盘数据

        将今日日志、近期统计以及过去3天的交易记录整理为结构化文本
        """
        now = datetime.now(self.eastern)
        date_str = now.strftime("%Y-%m-%d")

        parts = [
            f"## 复盘日期: {date_str} (美东时间 {now.strftime('%H:%M')})",
            f"\n## 标的池: {', '.join(WATCHLIST)}",
        ]

        # 过去3天的交易回顾（用于打脸分析）
        past_logs = self.trade_logger.get_recent_logs(days=3)
        parts.append("\n## 过去3日交易回顾 (用于策略审计)")
        for plog in past_logs:
            p_date = plog.get("date")
            p_trades = [t for t in plog.get("trades", []) if t.get("side") == "Sell"]
            if p_trades:
                parts.append(f"### 日期: {p_date}")
                for pt in p_trades:
                    parts.append(f"- 卖出 {pt['symbol']} @ {pt.get('price', '未知价格')} (理由: {pt.get('reason', 'N/A')})")

        # 风控评分历史
        risk_scores = today_log.get("risk_scores", [])
        if risk_scores:
            scores = [s["score"] for s in risk_scores]
            parts.append(f"\n## 今日风控评分")
            parts.append(f"- 次数: {len(scores)}")
            parts.append(f"- 范围: {min(scores)} ~ {max(scores)}")
            parts.append(f"- 平均: {sum(scores)/len(scores):.1f}")
            parts.append(f"- 最新: {risk_scores[-1]['score']} ({risk_scores[-1]['regime']})")
            parts.append(f"- 最新分项: {json.dumps(risk_scores[-1].get('components', {}), ensure_ascii=False)}")
        else:
            parts.append("\n## 今日风控评分: 无记录")

        # 决策记录
        decisions = today_log.get("decisions", [])
        if decisions:
            parts.append(f"\n## 今日决策记录 ({len(decisions)}条)")
            for i, d in enumerate(decisions, 1):
                parts.append(f"\n### 决策 #{i}")
                parts.append(f"- 时间: {d['timestamp']['eastern']}")
                parts.append(f"- 标的: {d['symbol']}")
                parts.append(f"- 动作: {d['action']}")
                parts.append(f"- 风控评分: {d['risk_score']}")
                parts.append(f"- 是否通过: {d['approved']}")
                parts.append(f"- 推理过程: {json.dumps(d.get('reasoning', {}), ensure_ascii=False)}")
        else:
            parts.append("\n## 今日决策记录: 无")

        # 交易记录
        trades = today_log.get("trades", [])
        if trades:
            parts.append(f"\n## 今日交易记录 ({len(trades)}笔)")
            for i, t in enumerate(trades, 1):
                parts.append(f"\n### 交易 #{i}")
                parts.append(f"- 时间: {t['timestamp']['eastern']}")
                parts.append(f"- {t['side']} {t['quantity']}股 {t['symbol']} @ {t.get('price', '市价')}")
                parts.append(f"- 理由: {t.get('reason', 'N/A')}")
        else:
            parts.append("\n## 今日交易记录: 无")

        # 错误记录
        errors = today_log.get("errors", [])
        if errors:
            parts.append(f"\n## 今日错误记录 ({len(errors)}条)")
            for e in errors:
                parts.append(f"- [{e['timestamp']['eastern']}] {e['context']}: {e['error']}")

        # 近7日统计
        parts.append(f"\n## 近7日统计")
        parts.append(json.dumps(recent_stats, ensure_ascii=False, indent=2))

        return "\n".join(parts)

    def _save_report(self, report: str, today_log: dict):
        """保存复盘报告到日志"""
        summary = {
            "report": report,
            "risk_score_count": len(today_log.get("risk_scores", [])),
            "decision_count": len(today_log.get("decisions", [])),
            "trade_count": len(today_log.get("trades", [])),
            "error_count": len(today_log.get("errors", [])),
        }
        self.trade_logger.save_daily_summary(summary)

    def _update_memory(self, report: str, today_log: dict):
        """
        从复盘报告中提取经验教训，压缩后写入记忆模块

        这是系统"自我进化"的关键步骤：
        1. 使用 LLM 从复盘中提取可执行规则
        2. 压缩每日摘要，保持记忆窗口
        3. 高优先级规则永久保留，低优先级按 FIFO 淘汰
        """
        try:
            self.log.info("开始更新交易记忆...")
            self.trading_memory.compress_and_store(
                llm=self.llm,
                review_report=report,
                today_log=today_log,
            )
            rules_count = self.trading_memory.get_rules_count()
            summaries_count = self.trading_memory.get_summaries_count()
            self.log.info(f"交易记忆更新完成: {rules_count} 条规则, {summaries_count} 天摘要")
        except Exception as e:
            self.log.error(f"交易记忆更新失败: {e}", exc_info=True)

    def _push_to_feishu(self, report: str):
        """推送复盘摘要到飞书"""
        if not self.feishu_notifier:
            return

        # 截取前2000字符作为摘要
        summary = report[:2000]
        if len(report) > 2000:
            summary += "\n\n... (报告已截断，完整报告见日志文件)"

        message = f"📊 【每日复盘报告】\n\n{summary}"

        try:
            self.feishu_notifier.send_text(message)
            self.log.info("复盘报告已推送飞书")
        except Exception as e:
            self.log.error(f"飞书推送失败: {e}")