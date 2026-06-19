# 红蓝对抗：三模式扩展可行性

> 红方 23 条攻击 · 蓝方逐条防守 · 2026-06-17

## 结论：可行，但需要三个前提改动

---

## 核心冲突

红方最致命的一条（1.1）："引擎不变，只改输入策略"是假命题。当前 Chief Editor 的提示词由 6 块 if-else 硬拼接，加 mode 意味着再加条件分支，引擎终究要动。

**蓝方方案**：用一个 `StrategyConfig` 把模式差异抽象出来，而不是在函数体内加 if-else。引擎的结构可以不改（图拓扑不变），但提示词注入方式要从"手工拼接"变成"config 驱动"。

---

## 必须先做的 3 个基础改动（做不完后面全卡住）

### 1. StrategyConfig — 策略配置抽象

**问题**：现在 Chief Editor 的 prompt 是 if-else 硬拼的。exam/practice/diagnostic 三种模式各用一块不同的提示词策略，不加抽象就膨胀到 9+ 块。

**做法**：`exam/config.py` 新增 `StrategyConfig` 数据类，封装 `mode`、`instruction_blocks`（哪些块启用）、`model_tier`、`min_count`、`max_count`。`chief_editor.py` 从 state 读 config 决定拼哪些块。

**改了**：不用改图拓扑，只改 prompt 组装方式。

### 2. 错题库接入

**问题**：现在 `_db_path` 是全局单例，只连 sections 库。mistakes 表在另一个库甚至同一个库都没法查。

**做法**：`agent_utils.py` 加 `get_weak_sections(student_id)` 工具——连 mistakes 库查该学生的错题，按章节聚合返回"3.2 就绪表：错了 3 次，类型=概念混淆"。这个工具注册到 Chief Editor 的工具集。

### 3. CLI 参数校验

**问题**：不加校验，`--mode practice --from-analysis report.json --count 50` 这种自相矛盾的组合一定会出现。

**做法**：`generate.py` 加 `validate_args()`：
- practice 无 `--student-id` → 报错退出
- diagnostic + `--count 50` → warning + 自动缩减
- practice + `--from-analysis` → warning + 忽略 analysis
- exam + `--focus` → focus 当"加强"而非"限制范围"

---

## 蓝图

```
┌─────────────────────────────────────────────────────┐
│                  generate.py CLI                      │
│  --mode exam|practice|diagnostic --student-id --db    │
│       │                                               │
│       ▼ validate_args() → GenerationConfig            │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────┐
│                 ExamGraph.propagate                   │
│  读 StrategyConfig → 构造 state → 构建图               │
│       │                                               │
│       ▼                                               │
│  ┌──────────────┐    ┌─────────────────────┐         │
│  │ Chief Editor  │◄───│ StrategyConfig       │         │
│  │ (提示词由      │    │ - mode              │         │
│  │  config驱动)   │    │ - instruction_blocks│         │
│  │               │    │ - model_tier        │         │
│  │ 工具集:        │    │ - constraints       │         │
│  │ search_keyword│    └─────────────────────┘         │
│  │ get_section   │                                    │
│  │ get_weak_     │  ← 新增（practice 模式）            │
│  │ sections      │                                    │
│  └──────────────┘                                    │
│       │                                               │
│       ▼ Fan-out (并发流水线不变)                        │
│  ┌─────────────────────────────────────┐             │
│  │ knowledge_extractor                 │             │
│  │   ↓ (diagnostic可跳过)              │             │
│  │ question_generator (5种题型,         │             │
│  │   diagnostic用quick模型)            │             │
│  │   ↓ (practice可跳过质检)            │             │
│  │ quality_reviewer (rejected重试)     │             │
│  └─────────────────────────────────────┘             │
│       │                                               │
│       ▼                                               │
│  ┌──────────────┐                                    │
│  │ Final Editor  │ → Markdown 试卷                    │
│  │ (标题标明模式) │                                    │
│  └──────────────┘                                    │
└──────────────────────────────────────────────────────┘
```

---

## 分批实现

### 第一批：打通最小链路（~100 行）

| # | 做啥 | 涉及文件 |
|---|------|---------|
| 1 | `GenerationConfig` + `StrategyConfig` | `exam/config.py` |
| 2 | `--mode` + `--student-id` + `validate_args()` | `generate.py` |
| 3 | `get_weak_sections` 工具 | `agent_utils.py` |
| 4 | Chief Editor 从 state 读 config 驱动 prompt | `chief_editor.py` |

做完 = exam 模式保持现有行为，practice 模式可以用错题库出定向练习卷。

### 第二批：性能 + 鲁棒（~80 行）

| # | 做啥 | 涉及文件 |
|---|------|---------|
| 5 | Diagnostic 用 quick_think_llm | `agent_utils.py` |
| 6 | LLM 调用 retry + backoff | `agent_utils.py` |
| 7 | 质检 rejected 重试 | `quality_reviewer.py` |
| 8 | section_id 格式化归一 | `agent_utils.py` |
| 9 | 空错题库 → fallback 出全本 easy 卷 | `chief_editor.py` |

### 第三批：体验 + 辅助（~100 行）

| # | 做啥 | 涉及文件 |
|---|------|---------|
| 10 | 试卷标题标模式 | `final_editor.py` |
| 11 | 并发限流（fan-out 分 5 批） | `setup.py` |
| 12 | 模式可选跳过知识提取/质检 | `setup.py` |
| 13 | `student_cli.py` 学生管理 | 新文件 |

---

## 红方最致命的 3 条（不修系统就废）

| # | 问题 | 不改的后果 |
|---|------|-----------|
| 1.1 | 无 StrategyConfig，prompt 靠 if-else 硬拼 | 三个模式加完 chief_editor.py 变成 400 行的 if-else 怪兽 |
| 1.2 | 无 get_weak_sections 工具 | Practice 模式根本读不到错题数据 |
| 3.1 | 无 --student-id | Practice 模式无法定位学生 |

这三条在第一批解决。
