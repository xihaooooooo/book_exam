# 三模式出题：扩展计划

## 核心原则

引擎架构不动（图拓扑、路由、生成器、质检器不变）。Chief Editor 的 prompt 加一段模式感知的策略指令（~16 行），final_editor 读 mode 调标题（~5 行），其余差异全部在 CLI 层通过参数推导解决。

```
                    ┌──────────────────┐
                    │   核心出题引擎     │
                    │  Chief Editor     │
                    │  + 策略指令(mode)  │  ← prompt 根据 mode 微调
                    │  + Fan-out 生成    │
                    │  + 质检 + 排版     │
                    └────────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
  ┌─────┴─────┐      ┌──────┴──────┐      ┌──────┴──────┐
  │ exam      │      │ practice    │      │ diagnostic  │
  │ (全本出题) │      │ (定向练习)   │      │ (诊断测评)   │
  │           │      │             │      │             │
  │ 策: 全覆盖 │      │ 策: 弱点多出  │      │ 策: 均匀覆盖 │
  │ 难度: 往年  │      │ 难度: 易→中   │      │ 难度: 全易   │
  └───────────┘      └─────────────┘      └─────────────┘
```

## 三种模式的定义

| 模式 | CLI | 做什么 | 选题策略 | 难度策略 |
|------|-----|--------|---------|---------|
| exam | `--mode exam` | 全本出题，对齐往年 | 优先核心概念章节，冷门可少出 | 往年比例，默认 3:4:3 |
| practice | `--mode practice --student S001` | 错题弱点补强 | 弱点章节出 70%，其余扫一遍 | 弱点 easy→medium 递进，其余 easy |
| diagnostic | `--mode diagnostic` | 初次摸底 | 有内容的章节每章都覆盖 | 全 easy |

三种模式的区别不只是参数预设，而是**选题偏好和难度策略不同**——这些需要在 Chief Editor 的 prompt 中体现。

## 实现方案

### 引擎改动（~22 行，3 个文件）

**1. `agent_states.py`**：AgentState 加 `mode` 字段（1 行）

**2. `chief_editor.py`**：system_message 拼接前加策略指令段（~16 行）

```python
mode = state.get("mode", "exam")

if mode == "diagnostic":
    strategy_instruction = """
### 0. 本次任务性质：诊断测评
- 摸底学生对全书各章节的基础掌握情况
- 有实质内容的章节尽量每章覆盖，不要整章跳过
- 题目难度全部设为 easy，不要出中等或困难题
- 题型限定选择题
"""
elif mode == "practice":
    strategy_instruction = """
### 0. 本次任务性质：定向练习
- 帮助学生补强薄弱章节
- 弱点章节（focus 指定）多出题，约占总量 70%
- 其余章节扫一遍（约占 30%），避免知识遗忘
- 弱点章节难度从 easy 开始，可逐步升到 medium
"""
else:
    strategy_instruction = ""  # exam 走现有逻辑
```

同时改造 `focus_instruction`：按 mode 区分语义。practice 模式时，focus 里就是精确的 section_id 列表（如 "2.1,3.4,5.2"），直接用 `get_section_text` 定位，不走 `search_keyword`；exam 模式走原有的全文搜索逻辑（~8 行）。

```python
if mode == "practice":
    focus_instruction = f"""
## 练习重点（系统根据错题库自动生成）

以下章节学生错误率较高，请直接在这些章节出题：
{focus}

用 get_section_text 读取各节内容后规划出题。
弱点章节约占 70%，其余章节扫一遍。
"""
else:
    # exam 模式：用户手写的 focus，走 search_keyword 搜索
    focus_instruction = f"""
## 考试重点（用户指定）

用户要求考试重点为：**{focus}**
...
"""
```

**3. `final_editor.py`**：读 state 中的 mode 调整试卷标题（~5 行）

| mode | 标题 |
|------|------|
| exam | 《XX》测试卷 |
| practice | 《XX》定向练习卷 |
| diagnostic | 《XX》诊断测评卷 |

### CLI 层（~80 行新增）

**`generate.py`** 加三个东西：

**`derive_params(mode, student_id, db_path, user_focus, user_count, user_types)`**：

```python
def derive_params(mode, student_id, db_path, user_focus, user_count, user_types):
    if mode == "exam":
        return user_focus or "", user_count or 0, user_types or ""

    if mode == "diagnostic":
        toc = _build_toc_from_db(db_path)
        chapter_count = len(toc)
        count = min(chapter_count * 2, 30)      # 上限 30 题
        count = max(count, 6)                    # 下限 6 题
        return "", count, "choice"

    if mode == "practice":
        weak = get_weak_sections(student_id)
        if not weak:
            print("警告：该学生错题库为空，降级为 exam 模式")
            return user_focus or "", user_count or 0, user_types or ""

        # 直接拼 section_id，practice 模式的 focus_instruction 知道怎么用
        focus = ",".join(w["section_id"] for w in weak)
        count = len(weak) * 2                     # 弱点章节数 × 2，不是 sum(error_count)
        count = min(count, 30)                    # 上限 30
        return focus, count, user_types or ""
```

**`validate_args(args, mode)`**：参数冲突检测

```python
def validate_args(args, mode):
    if mode == "practice" and not args.student:
        print("错误：practice 模式需要 --student 参数")
        sys.exit(1)
    if mode == "diagnostic" and args.count and args.count > 30:
        print("警告：diagnostic 模式题数过大，已缩减为 30")
    if mode in ("practice", "diagnostic") and args.from_analysis:
        print("警告：practice/diagnostic 模式忽略 --from-analysis")
    if mode == "diagnostic" and args.types and args.types != "choice":
        print("警告：diagnostic 模式强制 choice 题型，忽略 --types")
```

**CLI 参数**：加 `--mode`（默认 exam）、`--student`（practice 必填）

### 错题库（~50 行新增）

**分库**：`mistakes.db`（独立于 sections.db，避免备份/迁移互相影响）

```sql
CREATE TABLE students (id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE mistakes (
    id INTEGER PRIMARY KEY,
    student_id TEXT,
    exam_title TEXT,
    stem TEXT,
    wrong_answer TEXT,
    correct_answer TEXT,
    section_id TEXT,
    topic TEXT,
    error_pattern TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(student_id, exam_title, stem)   -- 防重复录入
);
```

**`agent_utils.py`** 加两个函数：

- `init_mistakes_db(db_path)` — 建表（`CREATE TABLE IF NOT EXISTS`，幂等，启动时自动调用）
- `get_weak_sections(student_id)` — 按 section_id 聚合，按错误次数降序，返回 section_id + topic + error_count

**`record_mistake.py`**（新文件）：支持单条录入和 `--batch answers.json` 批量导入

## 需要改动的文件

| 文件 | 改什么 | 行数 |
|------|--------|------|
| `agent_states.py` | 加 `mode` 字段 | 1 |
| `chief_editor.py` | 策略指令段 + focus_instruction 按 mode 分支 | ~16 |
| `final_editor.py` | 读 mode 调标题 | ~5 |
| `generate.py` | --mode + --student + derive_params + validate_args | ~80 |
| `agent_utils.py` | init_mistakes_db + get_weak_sections | ~40 |
| `record_mistake.py` | 新文件：错题录入（含批量） | ~60 |

## 不改的文件

`setup.py`、`conditional_logic.py`、`question_generator.py`、`quality_reviewer.py`、`knowledge_extractor.py`、`schemas.py`、`exam_graph.py`。

## 边缘场景处理

| 场景 | 处理 |
|------|------|
| practice + 空错题库 | 降级 exam 模式 + 警告 |
| practice + 不存在的 student_id | 报错退出 |
| diagnostic + 1 章教材 | 下限 6 题 |
| diagnostic + 40 章教材 | 上限 30 题 |
| diagnostic + --types short_answer | 警告 + 忽略，强制 choice |
| practice + --from-analysis | 警告 + 忽略 analysis |
| practice + 用户同时传 --focus | 用户显式值覆盖推导值 |
| exam + 不传 mode | 默认 exam，行为完全不变 |

## 实现步骤

### 第一步：错题库（~50 行）

- `agent_utils.py`：加 `init_mistakes_db()`、`get_weak_sections(student_id)`
- `mistakes.db` 建 students + mistakes 表
- `record_mistake.py`：单条 + 批量录入

### 第二步：引擎微调（~22 行）

- `agent_states.py`：加 mode 字段
- `chief_editor.py`：策略指令段 + focus_instruction 按 mode 分支
- `final_editor.py`：读 mode 调标题

### 第三步：CLI 模式推导 + 校验（~80 行）

- `generate.py`：加 `--mode`、`--student`、`derive_params()`、`validate_args()`

---

## 与可行性文件的差异说明

`three_mode_feasibility.md` 提出用 `StrategyConfig` 类 + `BLOCK_REGISTRY` 字典做 config 驱动。本计划采用更务实的方案：不加抽象层，直接在 prompt 拼接处加一段 mode 感知的策略指令（if-else 3 个分支）。理由：

- 当前只有 3 个模式，策略指令差异 ~8 行，if-else 完全可控
- StrategyConfig + 注册表在当前规模是过度抽象，控制流反而变间接
- 等模式超过 5 个、策略指令超过 50 行时再抽不迟
