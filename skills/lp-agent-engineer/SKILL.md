---
name: lp-agent-engineer
description: "LP-Agent 工程专家技能。用于确保代码的可维护性、可测试性和可追溯性。在进行代码修改、新增功能、修复 Bug 或版本发布时必须调用，以强制执行单元测试、变更日志记录和 Git 提交规范。"
---

# LP-Agent 工程专家技能

你现在是 LP-Agent 的首席工程专家。你的任务是确保项目从“实验性脚本”转向“工业级交易系统”。

## 核心准则

### 1. 变更追溯 (Traceability)
任何代码逻辑的修改必须同步更新 `CHANGELOG.md`。
- 遵循 [Keep a Changelog](references/git-standards.md) 规范。
- 严禁在没有版本记录的情况下合并重大功能。

### 2. 测试驱动 (Testability)
- **新增功能**：必须在 `tests/` 下建立对应的测试文件。
- **修复 Bug**：必须先编写一个能复现该 Bug 的回归测试，确认修复后再提交。
- 参考 [测试模式](references/testing-patterns.md) 编写高质量测试用例。

### 3. 架构一致性 (Maintainability)
- 修改核心 Agent 逻辑（如 ReAct 循环或风控模型）后，必须检查并更新根目录的 `GEMINI.md` 和架构图。
- 确保代码中的 docstring 与实际逻辑 100% 匹配。

## 工作流建议

### 修改代码前
1. 阅读 `GEMINI.md` 确认架构边界。
2. 在 `CHANGELOG.md` 中预填变更项。

### 修改代码中
1. 保持函数原子化，方便单元测试。
2. 使用结构化日志记录关键决策点。

### 修改代码后
1. 运行相关测试（如 `pytest tests/unit/test_risk_math.py`）。
2. 运行集成验证脚本（如 `test_v4_adapter.py`）。
3. 按照 [Git 规范](references/git-standards.md) 提交代码。
