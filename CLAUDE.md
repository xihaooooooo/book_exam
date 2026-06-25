# CLAUDE.md

## 项目概述

期末大学生速通系统——基于 LangGraph 的本地 Agent 项目。核心是三个独立的 Agent 共享教材知识库和学生数据库：

```
                     sections.db（教材知识库）
                           │
    ┌──────────────────────┼──────────────────────┐
    │                      │                      │
    ▼                      ▼                      ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  ExamGraph  │   │ JudgeGraph  │   │ProfileGraph │
│             │   │             │   │             │
│ strategy_   │   │  judge_all  │   │  build_     │
│  router     │   │  ├ choice   │   │  profile    │
│    ↓        │   │  │ fill     │   │             │
│ chief_      │   │  │ code_fill│   │ 从 attempts │
│  editor     │   │  │ →文本规则│   │ + error_    │
│    ↓        │   │  │          │   │ labels 实时 │
│ generator×5 │   │  └ short    │   │ 聚合画像    │
│  (并发)     │   │   /compreh  │   └─────────────┘
│    ↓        │   │   →LLM并发  │
│ quality_    │   └─────────────┘
│  reviewer   │          │
│    ↓        │          ▼
│ final_      │    attempts.db + error_labels
│  editor     │
└─────────────┘
    │
    ▼
web/index.html → /api/generate → ExamGraph → 答题 → /api/submit-exam → JudgeGraph
```

- **ExamGraph**：出题 Agent。由 Web/API 调用，strategy_router（按模式推导策略）→ chief_editor（选题/搜知识点）→ 5 个 generator 并发 → quality_reviewer 质检 → final_editor 排版
- **JudgeGraph**：判题 Agent。由 `/api/submit-exam` 调用，judge_all 单节点，纯文本题直接判，简答/综合题 asyncio.gather 并发 LLM 语义判定
- **ProfileGraph**：画像 Agent。由 `/api/profile` 调用，实时从 attempts + error_labels 聚合掌握等级/风险信号/错因分布

三种出题模式只通过 Web/API 暴露：exam（全书/按知识点）、diagnostic（摸底，每章 2 道 easy 选择题）、practice（基于 BKT + Bandit + 长期记忆闭环的薄弱练习）。

## 产品入口

- 唯一用户入口：`web/index.html`
- 服务入口：`python web/server.py`
- 出题、答题、画像、往年试卷分析都走 Web/API。
- 不再保留学习闭环 CLI：出题、交卷、画像、作答记录和错题录入不得绕过 Web/API。
- `parse.py` 仍作为教材入库/数据准备工具保留；它不属于学生学习闭环。
- **单用户设计**：`student_id = "default"` 是内部数据库归属键，不是产品功能。Web/API 层对用户完全隐藏学生 ID 概念，所有接口内部统一使用该常量。不建议删除数据库中的 `student_id` 列（影响面大收益小），但新功能不得在产品层暴露该字段。

## 规则

1. **改代码前必须先说明**：改动内容、原因、影响范围都说清楚，用户同意后才能动手。不自行改代码。
2. **改完不要自动跑测试**：代码改完后不自动运行测试或验证脚本，等用户明确说"跑"再跑。
3. 这是一个基于 LangGraph 实现的本地 Agent 项目。
