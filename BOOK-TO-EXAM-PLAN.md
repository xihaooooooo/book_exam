# Book-to-Exam 项目实现计划

## 概述

基于 TradingAgents 的多智能体架构，构建一个"书本 → 试卷"生成工具。
- **输入**：PDF 书本
- **输出**：完整试卷（选择题 + 填空题 + 简答题 + 答案）
- **技术栈**：Python + LangGraph + Web UI
- **定位**：个人学习工具

## 核心架构

借鉴 TradingAgents 的 LangGraph StateGraph + Send 并发模式：

```
PDF解析(目录) → 主编(前期) → N×并发生成流水线(中期) → 终审(后期) → 试卷输出
```

---

## 一、项目结构

```
book-to-exam/
├── main.py                          # 快速入口
├── pyproject.toml
├── .env.example
│
├── app/                             # Web UI
│   ├── __init__.py
│   ├── main.py                      # FastAPI 入口
│   ├── static/                      # 前端静态文件
│   └── templates/                   # Jinja2 模板
│
├── exam/                            # 核心包
│   ├── __init__.py
│   ├── default_config.py            # 全局配置（借鉴TradingAgents）
│   │
│   ├── graph/                       # LangGraph 图编排
│   │   ├── __init__.py
│   │   ├── exam_graph.py            # 主编排器 ExamGraph（类比TradingAgentsGraph）
│   │   ├── setup.py                 # 图节点/边构建（类比setup.py）
│   │   ├── conditional_logic.py     # 条件路由逻辑
│   │   └── propagation.py           # 状态初始化
│   │
│   ├── agents/                      # Agent 定义
│   │   ├── __init__.py
│   │   ├── schemas.py               # Pydantic 结构化输出
│   │   │   ├── QuestionTask         # 单题任务定义
│   │   │   ├── ExamPlan             # 出题计划
│   │   │   ├── GeneratedQuestion    # 生成的题目
│   │   │   └── FinalExam            # 最终试卷
│   │   │
│   │   ├── planner/                 # 前期 Agent
│   │   │   ├── __init__.py
│   │   │   └── chief_editor.py      # 主编：读目录 → 产出任务清单
│   │   │
│   │   ├── generators/              # 中期 Agent（每道题一条流水线）
│   │   │   ├── __init__.py
│   │   │   ├── knowledge_extractor.py  # 知识点提取
│   │   │   ├── question_generator.py   # 题目生成
│   │   │   └── quality_reviewer.py     # 质检审核
│   │   │
│   │   ├── reviewers/               # 后期 Agent
│   │   │   ├── __init__.py
│   │   │   └── final_editor.py      # 终审排版师
│   │   │
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── agent_states.py      # AgentState TypedDict
│   │       └── agent_utils.py       # 共享工具函数
│   │
│   ├── dataflows/                   # 数据处理层
│   │   ├── __init__.py
│   │   ├── pdf_parser.py            # PDF 解析（目录 + 章节文本）
│   │   └── text_utils.py            # 文本分块、清理
│   │
│   └── llm_clients/                 # LLM 客户端（直接复用TradingAgents的）
│       ├── __init__.py
│       ├── factory.py
│       ├── base_client.py
│       ├── model_catalog.py
│       ├── api_key_env.py
│       ├── openai_client.py
│       ├── anthropic_client.py
│       └── google_client.py
│
└── tests/
    └── ...
```

---

## 二、LangGraph 图结构

### AgentState

```python
class AgentState(MessagesState):
    pdf_path: str                    # PDF 文件路径
    toc: list[dict]                  # 目录结构 [{chapter, sections: [...]}]
    exam_plan: ExamPlan              # 主编产出的出题计划
    questions: list[GeneratedQuestion]  # 所有生成的题目
    final_exam: FinalExam            # 最终试卷
    config: dict                     # 运行时配置
```

### 节点与边

```
START
  │
  ▼
[PDF解析] ─── 非LLM节点，提取目录和章节文本
  │
  ▼
[主编] ─── LLM节点，读目录 → 产出ExamPlan（任务清单）
  │
  ├─→ Send("生成流水线", {"task": task1})
  ├─→ Send("生成流水线", {"task": task2})
  ├─→ Send("生成流水线", {"task": task3})
  │    ...（N 并发）
  │
  ▼
[汇总节点] ─── 收集所有生成的题目
  │
  ▼
[终审排版师] ─── LLM节点，去重/调难度/排版
  │
  ▼
END
```

### 每条生成流水线内部（3 步串行）

```
知识点提取 ─── 读章节正文 → 提取具体知识点
  │
  ▼
题目生成 ─── 根据知识点 + 题型 → 生成题目和答案
  │
  ▼
质检审核 ─── 检查答案正确性 + 干扰项合理性 + 题目清晰度
  │
  ▼
返回 GeneratedQuestion
```

---

## 三、Agent 详细设计

### 3.1 主编 (chief_editor.py)

- **输入**：PDF 目录结构（章节标题）
- **输出**：`ExamPlan`（任务清单）
  ```python
  class ExamPlan(BaseModel):
      tasks: list[QuestionTask]
      difficulty_ratio: tuple[int, int, int]  # 易:中:难
      total_score: int

  class QuestionTask(BaseModel):
      chapter: str
      section: str
      topic: str              # 知识点评述
      question_type: Literal["choice", "fill_blank", "short_answer"]
      difficulty: Literal["easy", "medium", "hard"]
      score: int
  ```
- **工具**：可选，读完目录如果判断不了，可以 peek 一下章节开头几段
- **LLM**：深度思考模型

### 3.2 知识点提取器 (knowledge_extractor.py)

- **输入**：单个 QuestionTask + 对应章节完整文本
- **输出**：精炼的知识点描述（包含关键事实、定义、易错点）
- **工具**：`get_section_text` —— 按章节标题获取正文
- **LLM**：快速思考模型

### 3.3 题目生成器 (question_generator.py)

- **输入**：知识点 + QuestionTask（题型、难度）
- **输出**：`GeneratedQuestion`
  ```python
  class GeneratedQuestion(BaseModel):
      question_type: Literal["choice", "fill_blank", "short_answer"]
      difficulty: Literal["easy", "medium", "hard"]
      stem: str                        # 题干
      options: list[str] | None        # 选择题选项
      correct_answer: str              # 正确答案
      explanation: str                 # 解题思路/答案解析
      source: str                      # 来源章节
  ```
- **LLM**：深度思考模型（出题需要较好的推理能力）

### 3.4 质检审核员 (quality_reviewer.py)

- **输入**：GeneratedQuestion
- **输出**：审核通过的 GeneratedQuestion，或退回修改
- **检查项**：答案正确性、干扰项合理、题干清晰度、难度匹配度
- **LLM**：快速思考模型

---

## 四、并发设计

使用 LangGraph `Send` API 实现 fan-out 并发：

```python
from langgraph.types import Send

def fan_out_to_generation(state: AgentState):
    """主编完成后，每个任务发一条独立流水线"""
    return [
        Send("generation_pipeline", {"task": task})
        for task in state["exam_plan"].tasks
    ]

workflow.add_conditional_edges("chief_editor", fan_out_to_generation, ["generation_pipeline"])
```

每条 `generation_pipeline` 内部是子图（3 节点串行：提取 → 生成 → 质检）。

---

## 五、Web UI 设计

使用 FastAPI + 简单前端：

### 页面流程
1. **上传页**：拖拽上传 PDF
2. **配置页**：选择题型比例、难度分布、题目总数、LLM 提供商
3. **进度页**：实时展示进度（章节解析 → 出题中 → 质检中 → 生成完成）
4. **结果页**：展示完整试卷 + 答案，支持下载 PDF/打印

### 实时进度
使用 WebSocket 或 SSE 推送 LangGraph 的执行进度（借鉴 TradingAgents CLI 的 Rich Live Display 思路）。

---

## 六、与 TradingAgents 的核心复用

| 模块 | 复用方式 |
|------|----------|
| LLM 客户端 (`llm_clients/`) | 直接复用，13 种提供商支持 |
| 图编排模式 (`graph/setup.py`) | 复用 StateGraph + Send + conditional_edges 模式 |
| 配置系统 (`default_config.py`) | 复用环境变量覆盖 + 默认配置模式 |
| Agent 工厂模式 (`agents/`) | 复用 create_*_analyst 函数模式 |
| 结构化输出 (`agents/schemas.py`) | 复用 Pydantic + structured_output 模式 |
| 工具绑定模式 | 复用 @tool + bind_tools + ToolNode 模式 |

---

## 七、实现步骤

### 第一阶段：最小可运行版本
1. 搭项目骨架（pyproject.toml, 目录结构）
2. 实现 PDF 解析层（目录提取 + 章节文本获取）
3. 实现 LLM 客户端层（复用 TradingAgents）
4. 实现主编 Agent（读目录 → 产出任务清单）
5. 实现单条生成流水线（知识点提取 → 题目生成 → 质检）
6. 用 LangGraph 串联，先串行跑通

### 第二阶段：并发 & 后期
7. 接入 Send API 实现并发生成
8. 实现终审排版 Agent
9. 实现试卷输出（Markdown 格式化）

### 第三阶段：Web UI
10. FastAPI 后端 + SSE 进度推送
11. 前端上传/配置/结果页面
12. 试卷下载功能

---

## 八、验证方式

1. 准备一本测试 PDF（如技术教材的一个章节）
2. 运行 `python main.py` 传入 PDF 路径
3. 检查日志：主编产出的任务清单是否合理
4. 检查输出：生成的题目是否覆盖了关键知识点
5. 人工抽查 5-10 道题，验证答案正确性
6. 运行 `pytest` 检查所有单元测试通过
