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
questions_*.json → quiz.html → 学生答题 → POST /api/submit-exam → JudgeGraph
```

- **ExamGraph**：出题 Agent。strategy_router（按模式推导策略）→ chief_editor（选题/搜知识点）→ 5 个 generator 并发 → quality_reviewer 质检 → final_editor 排版
- **JudgeGraph**：判题 Agent。judge_all 单节点，纯文本题直接判，简答/综合题 asyncio.gather 并发 LLM 语义判定
- **ProfileGraph**：画像 Agent。build_profile 单节点，实时从 attempts + error_labels 聚合掌握等级/风险信号/错因分布

三种出题模式：exam（全书/按知识点）、diagnostic（摸底，每章 2 道 easy 选择题）、practice（薄弱点，TODO：接入 ProfileGraph 的完整画像替代简单 SQL 聚合）

## 规则

1. **改代码前必须先说明**：改动内容、原因、影响范围都说清楚，用户同意后才能动手。不自行改代码。
2. **改完不要自动跑测试**：代码改完后不自动运行测试或验证脚本，等用户明确说"跑"再跑。
3. 这是一个基于 LangGraph 实现的本地 Agent 项目。
