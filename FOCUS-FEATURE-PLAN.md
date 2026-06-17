# Focus 出题功能 实现计划

## 目标

`generate.py` 加三个参数：`--focus`（考试重点）、`--count`（题数）、`--types`（题型）。

## 新增用法

```bash
python generate.py                                    # 全本出题（当前行为）
python generate.py --focus "中断处理"                   # 指定考点
python generate.py --focus "信号量" --count 5           # 5 道题
python generate.py --focus "任务" --types choice,fill_blank  # 只要选择题+填空题
python generate.py --count 8 --types short_answer       # 只要简答题
```

## 涉及文件

| 文件 | 改动 |
|---|---|
| `generate.py` | 加三个 CLI 参数，传入 `propagate` |
| `exam/graph/exam_graph.py` | `propagate` 加 `focus`/`target_count`/`allowed_types` |
| `exam/agents/planner/chief_editor.py` | 系统提示词加 focus 相关指令 |

## 实现细节

### Step 1 — `generate.py` 加参数

```python
parser.add_argument("--focus", default=None)
parser.add_argument("--count", type=int, default=None)
parser.add_argument("--types", default=None)  # "choice,fill_blank"
```

传入 `propagate`：

```python
exam.propagate(
    db_path=db_path,
    toc=toc,
    focus=args.focus,
    target_count=args.count,
    allowed_types=args.types,
)
```

### Step 2 — `exam_graph.py` 透传参数

`propagate()` 把参数写入 `initial_state`：

```python
def propagate(self, db_path=None, toc=None,
              focus=None, target_count=None, allowed_types=None):
    ...
    initial_state = self._create_initial_state(
        toc, focus, target_count, allowed_types
    )
    ...

def _create_initial_state(self, toc, focus, target_count, allowed_types):
    return {
        ...
        "focus": focus or "",
        "target_count": target_count or 0,
        "allowed_types": allowed_types or "",
    }
```

### Step 3 — `agent_states.py` 加字段

```python
class AgentState(MessagesState):
    ...
    focus: str
    target_count: int
    allowed_types: str
```

### Step 4 — `chief_editor.py` 改造提示词

在现有 system_message 前插入 focus 相关指令：

```python
focus_text = ""
if focus := state.get("focus", ""):
    focus_text = f"""
## 特殊要求：以下考试重点由用户指定

{ focus }

请按以下步骤组织出题计划：

1. 用 search_keyword 搜索上述关键词，定位相关章节
2. 如果匹配章节 < 3 节，用 get_surrounding_context 扩展到相邻节
3. 如果匹配章节 > 10 节，用 peek_section 看内容后精选最核心的 8 节
4. 只在选中的章节内出题

"""

count_text = ""
if target_count := state.get("target_count", 0):
    count_text = f"\n总题数：恰好 {target_count} 道。\n"

types_text = ""
if allowed := state.get("allowed_types", ""):
    types_text = f"\n题型限制：只允许 {allowed}。\n"
```

### Step 5 — 题数自适应逻辑

总编提示词里加：

```
如果用户指定了题数 → 严格按指定题数出
如果用户没指定题数 → 按章节数量自动计算：
  - 覆盖节数 ≤ 4  → 每节 2 道
  - 覆盖节数 5-8  → 每节 1-2 道
  - 覆盖节数 > 8  → 每节 1 道
  - 总题数控制在 6-12 道
```

## 改动量

| 文件 | 改动行数 |
|---|---|
| `generate.py` | +6 |
| `exam_graph.py` | +5 |
| `agent_states.py` | +3 |
| `chief_editor.py` | +25 |

总计 **~40 行**，不改图结构、不改其他 Agent。

## 验证方式

```bash
# 1. 全本出题（无参数，行为不变）
python generate.py

# 2. 指定考点
python generate.py --focus "任务就绪表"

# 3. 考点 + 题数 + 题型
python generate.py --focus "中断处理,信号量" --count 5 --types choice
```
