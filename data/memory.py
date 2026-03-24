"""
交易记忆模块
存储和压缩每日复盘中的经验教训，供后续交易决策参考

设计思路:
  1. 每日复盘后，调用 LLM 从复盘报告中提取可执行的经验规则
  2. 同时存储最近 N 天的压缩日报摘要
  3. ReAct Agent 每轮读取记忆，注入 prompt 作为历史经验

存储结构:
  data/memory.json - 单文件持久化
  {
    "learned_rules": [...],        # 提炼的交易规则（长期保留）
    "daily_summaries": [...],      # 每日压缩摘要（滚动窗口）
    "last_updated": "2026-03-23"
  }
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

from llm.base import BaseLLM, ChatMessage, Role


logger = logging.getLogger("TradingMemory")

MEMORY_FILE = Path(__file__).parent / "memory.json"

# 用于从复盘报告中提取经验规则的 prompt
MEMORY_EXTRACTION_PROMPT = """你是一个交易系统的经验提取器。你的任务是从每日复盘报告中提取可执行的经验教训。

## 提取要求

1. **learned_rules**: 从复盘中提炼出 1-3 条最重要的、可执行的交易规则或教训。每条规则需要：
   - rule: 清晰、具体、可直接执行的规则描述（不超过50字）
   - category: 分类（risk_management / entry_strategy / exit_strategy / position_sizing / market_timing）
   - priority: 优先级（high / medium / low）
   - context: 简要说明这条规则是从什么场景中学到的（不超过30字）

2. **compressed_summary**: 将整篇复盘报告压缩为不超过200字的精华摘要，保留关键数据和结论。

## 输出格式（严格 JSON）

```json
{
  "learned_rules": [
    {
      "rule": "...",
      "category": "...",
      "priority": "...",
      "context": "..."
    }
  ],
  "compressed_summary": "..."
}
```

注意：
- 如果某条教训与已有规则高度重复（见下方"已有规则"），则不要重复提取，除非有重要补充
- 优先提取那些能直接改变交易行为的规则，而不是泛泛的"要小心"之类
- 输出纯 JSON，不要有任何额外文字
"""


class TradingMemory:
    """
    交易记忆管理器

    负责记忆的存储、压缩、检索，是系统"自我进化"的核心模块。
    """

    def __init__(
        self,
        memory_file: Optional[Path] = None,
        max_daily_summaries: int = 15,
        max_rules: int = 50,
    ):
        self.memory_file = memory_file or MEMORY_FILE
        self.max_daily_summaries = max_daily_summaries
        self.max_rules = max_rules
        self.eastern = pytz.timezone("US/Eastern")

    def _load_memory(self) -> dict:
        """加载记忆文件"""
        if self.memory_file.exists():
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"读取记忆文件失败: {e}，创建新记忆")

        return {
            "version": 1,
            "last_updated": None,
            "learned_rules": [],
            "daily_summaries": [],
        }

    def _save_memory(self, memory: dict):
        """保存记忆文件"""
        memory["last_updated"] = datetime.now(self.eastern).strftime("%Y-%m-%d %H:%M:%S")
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)

    # ═══════════════════════════════════════
    # 写入：复盘后压缩存储
    # ═══════════════════════════════════════

    def compress_and_store(
        self,
        llm: BaseLLM,
        review_report: str,
        today_log: dict,
        date_str: Optional[str] = None,
    ):
        """
        从复盘报告中提取经验并存入记忆

        由 ReviewAgent 在完成复盘后调用。
        使用 LLM 从复盘报告中提取可执行规则和压缩摘要。

        Args:
            llm: LLM 实例（用于提取经验）
            review_report: 完整的复盘报告文本
            today_log: 当日交易日志
            date_str: 日期字符串（默认今天）
        """
        date_str = date_str or datetime.now(self.eastern).strftime("%Y-%m-%d")
        memory = self._load_memory()

        logger.info(f"开始从复盘报告中提取记忆 ({date_str})")

        # 构建已有规则文本（避免重复提取）
        existing_rules_text = ""
        if memory["learned_rules"]:
            existing_rules_text = "\n## 已有规则\n"
            for r in memory["learned_rules"][-20:]:  # 只展示最近20条
                existing_rules_text += f"- [{r['category']}] {r['rule']}\n"

        # 构建交易统计摘要
        trades = today_log.get("trades", [])
        risk_scores = today_log.get("risk_scores", [])
        trade_summary = f"今日交易 {len(trades)} 笔"
        if risk_scores:
            scores = [s["score"] for s in risk_scores]
            trade_summary += f"，风控评分 {min(scores):.0f}~{max(scores):.0f}"

        # 调用 LLM 提取经验
        messages = [
            ChatMessage(
                role=Role.SYSTEM,
                content=MEMORY_EXTRACTION_PROMPT + existing_rules_text,
            ),
            ChatMessage(
                role=Role.USER,
                content=f"日期: {date_str}\n{trade_summary}\n\n复盘报告:\n{review_report}",
            ),
        ]

        try:
            response = llm.chat(messages, tools=None)
            extracted = self._parse_extraction(response.content)

            if extracted:
                # 存储新规则
                new_rules = extracted.get("learned_rules", [])
                for rule in new_rules:
                    rule["date_learned"] = date_str
                    rule["source"] = f"Review {date_str}"
                    memory["learned_rules"].append(rule)

                # 裁剪规则数量
                if len(memory["learned_rules"]) > self.max_rules:
                    # 保留高优先级规则 + 最近的规则
                    high_priority = [r for r in memory["learned_rules"] if r.get("priority") == "high"]
                    others = [r for r in memory["learned_rules"] if r.get("priority") != "high"]
                    memory["learned_rules"] = high_priority + others[-(self.max_rules - len(high_priority)):]

                # 存储每日压缩摘要
                compressed = extracted.get("compressed_summary", "")
                memory["daily_summaries"].append({
                    "date": date_str,
                    "summary": compressed,
                    "trades_count": len(trades),
                    "risk_score_avg": round(sum(s["score"] for s in risk_scores) / len(risk_scores), 1) if risk_scores else None,
                    "new_rules_count": len(new_rules),
                })

                # 裁剪摘要窗口
                if len(memory["daily_summaries"]) > self.max_daily_summaries:
                    memory["daily_summaries"] = memory["daily_summaries"][-self.max_daily_summaries:]

                self._save_memory(memory)
                logger.info(f"记忆更新完成: 新增 {len(new_rules)} 条规则，总规则 {len(memory['learned_rules'])} 条")
            else:
                logger.warning("LLM 提取结果解析失败，跳过记忆更新")

        except Exception as e:
            logger.error(f"记忆提取失败: {e}", exc_info=True)
            # 即使 LLM 提取失败，也存一个基础摘要
            memory["daily_summaries"].append({
                "date": date_str,
                "summary": review_report[:200],
                "trades_count": len(trades),
                "risk_score_avg": round(sum(s["score"] for s in risk_scores) / len(risk_scores), 1) if risk_scores else None,
                "new_rules_count": 0,
            })
            if len(memory["daily_summaries"]) > self.max_daily_summaries:
                memory["daily_summaries"] = memory["daily_summaries"][-self.max_daily_summaries:]
            self._save_memory(memory)

    def _parse_extraction(self, content: str) -> Optional[dict]:
        """解析 LLM 提取结果"""
        if not content:
            return None
        try:
            # 尝试直接解析
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # 尝试从 markdown 代码块中提取
        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(content[start:end])
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    # ═══════════════════════════════════════
    # 读取：为 ReAct Agent 提供记忆上下文
    # ═══════════════════════════════════════

    def get_memory_context(self, max_rules: int = 20, max_summaries: int = 5) -> str:
        """
        获取格式化的记忆上下文文本，用于注入 ReAct 提示词

        Args:
            max_rules: 最多返回多少条规则
            max_summaries: 最多返回多少天的摘要

        Returns:
            格式化的记忆文本（如果无记忆返回空字符串）
        """
        memory = self._load_memory()

        rules = memory.get("learned_rules", [])
        summaries = memory.get("daily_summaries", [])

        if not rules and not summaries:
            return ""

        parts = []

        # 输出规则（按优先级排序）
        if rules:
            # 高优先级在前
            priority_order = {"high": 0, "medium": 1, "low": 2}
            sorted_rules = sorted(rules, key=lambda r: priority_order.get(r.get("priority", "low"), 2))
            display_rules = sorted_rules[:max_rules]

            parts.append("### 历史经验规则（从过往复盘中提炼）")
            for i, r in enumerate(display_rules, 1):
                priority_tag = "‼️" if r.get("priority") == "high" else "⚠️" if r.get("priority") == "medium" else "ℹ️"
                parts.append(
                    f"{i}. {priority_tag} [{r.get('category', '?')}] {r['rule']}"
                    f"  (来源: {r.get('source', '?')})"
                )

        # 输出近期摘要
        if summaries:
            display_summaries = summaries[-max_summaries:]
            parts.append("\n### 近期交易摘要")
            for s in reversed(display_summaries):
                score_text = f"风控均分 {s['risk_score_avg']}" if s.get('risk_score_avg') else "无评分"
                parts.append(
                    f"- **{s['date']}**: {s.get('summary', 'N/A')} "
                    f"({s.get('trades_count', 0)}笔交易, {score_text})"
                )

        return "\n".join(parts)

    # ═══════════════════════════════════════
    # 手动管理接口
    # ═══════════════════════════════════════

    def add_rule_manually(
        self,
        rule: str,
        category: str = "risk_management",
        priority: str = "high",
        context: str = "手动添加",
    ):
        """
        手动添加一条经验规则（用于初始化或人工干预）
        """
        memory = self._load_memory()
        memory["learned_rules"].append({
            "rule": rule,
            "category": category,
            "priority": priority,
            "context": context,
            "date_learned": datetime.now(self.eastern).strftime("%Y-%m-%d"),
            "source": "手动添加",
        })
        self._save_memory(memory)
        logger.info(f"手动添加规则: {rule}")

    def get_rules_count(self) -> int:
        """获取当前规则总数"""
        memory = self._load_memory()
        return len(memory.get("learned_rules", []))

    def get_summaries_count(self) -> int:
        """获取当前摘要总数"""
        memory = self._load_memory()
        return len(memory.get("daily_summaries", []))


# 全局单例
_trading_memory: Optional[TradingMemory] = None


def get_trading_memory() -> TradingMemory:
    """获取交易记忆单例"""
    global _trading_memory
    if _trading_memory is None:
        _trading_memory = TradingMemory()
    return _trading_memory
