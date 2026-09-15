# 期末大学生速通系统：项目归档说明

> 归档日期：2026-09-13
> 原项目目录：`D:\trae\book_exam`
> 原远程仓库：<https://github.com/xihaooooooo/book_exam.git>
> 本文来自对项目源码、说明文档、规划文档和评测基线的静态阅读，不包含 `.env` 密钥、教材正文或学生作答明细。

## 1. 一句话概括

这是一个面向单个学生的本地考试学习系统：把 PDF 教材解析成可检索的 SQLite 知识库，利用大模型生成试卷，完成在线答题和判题，再根据历史作答构建知识掌握画像，并推荐下一轮针对性练习。

项目试图形成如下闭环：

```text
教材导入 → 自动出题 → 在线答题 → 判题与错因诊断
    ↑                                  ↓
下一轮个性化练习 ← BKT 画像与 Bandit 推荐 ← 作答记录
```

## 2. 项目想解决的问题

项目的核心目标不是单次生成题目，而是把教材内容变成一套可持续使用的复习流程：

- 从指定教材中提取章节、正文、公式和图片信息；
- 按考试、摸底或薄弱练习三种目的生成题目；
- 让学生在浏览器中完成答题；
- 客观题使用规则判定，主观题使用大模型进行语义判定；
- 对错误答案补充错因、证据和改善建议；
- 用 BKT 估计每个小节的掌握概率；
- 用 Bandit 排序薄弱知识点，决定下一轮练什么；
- 比较练习前后的掌握概率，把效果反馈到后续推荐。

这是一个单用户本地应用。当前代码已经移除了学生 ID 作为业务身份的设计。

## 3. 用户入口和主要流程

### 教材导入

用户可以通过 Web 上传 PDF，也可以运行 `parse.py`。PDF 经本地解析或 MinerU 服务处理后，章节结构和正文写入 `sections.db`；图片及媒体描述写入相应的图片目录和 manifest。项目支持多教材注册，每本书拥有独立的教材库、作答库、输出目录和分析目录。

### 自动出题

Web 的 `/api/generate` 创建后台生成任务。出题流程大致为：

```text
GenerationRequest
  → strategy_router 推导模式策略
  → chief_editor 规划题目清单
  → 每道题并发进入生成流水线
      → knowledge_extractor 查询教材
      → 按题型选择生成器
      → deterministic gate + LLM quality review
      → 接受、内容重试或拒绝
  → final_editor 汇总、排序并生成整卷
```

支持五种题型：选择题、填空题、简答题、代码填空题、综合题。题目可以携带图片、Mermaid、Canvas 或 LaTeX 内容。

### 在线答题和判题

浏览器读取当前题目，提交到 `/api/submit-exam`：

- 选择题和填空题优先使用本地规则判断；
- 简答题、综合题和代码填空题使用大模型进行语义判断；
- 客观题答错时可额外调用大模型诊断错因；
- 大模型不可用、超时或输出无法解析时，降级为保守的本地匹配；
- 判题结果批量写入 `attempts.db`，同时记录错因标签并完成学习 session。

### 画像和推荐

Web 的 `/api/profile` 直接调用画像与推荐服务，而不是依赖 LangGraph。服务从作答、错因、session 和快照中聚合：

- 每个教材小节的正确率、最近表现和掌握等级；
- BKT 连续掌握概率 `P(L)`；
- 主要错因和风险信号；
- 掌握度变化趋势；
- 长期记忆事实；
- 下一轮练习的章节、题型、难度和题量。

## 4. 三种出题模式

| 模式 | 含义 | 主要策略 |
|---|---|---|
| `exam` | 正式试卷 | 全书或指定范围覆盖，可参考往年试卷分析 |
| `diagnostic` | 摸底检测 | 每章生成少量简单选择题，快速发现薄弱点 |
| `practice` | 个性化练习 | 根据 BKT 掌握度、错因、趋势和 Bandit 排序集中训练 |

往年试卷可通过 `/api/analyze-exam` 上传 DOCX，分析题型、知识点和难度分布，作为正式出题的参考约束。

## 5. 核心架构

### Web 层

- `web/server.py`：使用标准库 `HTTPServer` 和 `SimpleHTTPRequestHandler` 实现静态页面及 JSON API；生成和教材解析任务通过后台线程执行，任务状态保存在进程内存中。
- `web/index.html`、`web/quiz.html`、`web/profile.html`：出题、答题和画像页面。
- `web/js/all.js`、`web/css/main.css`：主要前端逻辑和样式。

后台任务不是持久任务：服务进程重启后，内存中的任务状态会丢失。

### 教材层

- `parse.py`：教材入库入口。
- `exam/pdf_parser.py`：PDF 章节与正文解析，写入 SQLite。
- `exam/mineru.py`：MinerU 解析服务接入。
- `exam/parsers/docx_parser.py`：往年试卷 DOCX 解析。
- `exam/book_registry.py`：多教材注册表和每本书的存储路径管理。

### 出题层

- `exam/graph/exam_graph.py`：正式出题外观接口，初始化状态、编译图、执行并保存结果。
- `exam/graph/setup.py`：LangGraph 主图、动态 fan-out 和单题子图。
- `exam/graph/strategy.py`：三模式策略路由。
- `exam/agents/planner/chief_editor.py`：使用教材查询工具制定整卷计划。
- `exam/agents/generators/knowledge_extractor.py`：为单题检索教材依据。
- `exam/agents/generators/question_generator.py`：五种题型的结构化生成器。
- `exam/agents/generators/quality_reviewer.py`：确定性校验、语义审核和重试分类。
- `exam/agents/reviewers/final_editor.py`：确定性汇总、质量状态和 Markdown 排版。

`GenerationRequest`、`ExamPlan`、`QuestionTask` 和 `QuestionDraft` 是这条链路中较清晰的不可变领域对象。

### 判题层

- `exam/graph/judge_graph.py`：规则判题、LLM 判题、错因诊断、超时与降级。

虽然名为 Graph，当前只有一个 LangGraph 节点；真正的批量并发由 `asyncio.gather`、线程池和两个 Semaphore 完成。

### 学生画像层

- `exam/student_profile/storage.py`：作答和错因标签存储。
- `profile_engine.py`：按小节聚合画像并回放 BKT。
- `recommendation.py`：Bandit 状态、Thompson Sampling 和推荐计划。
- `recommendation_service.py`：统一装配画像页与练习模式所需的推荐上下文。
- `session_service.py`、`session_storage.py`：学习 session、前后快照和效果闭环。
- `trend_engine.py`：掌握度变化和趋势。
- `memory_engine.py`：长期薄弱点记忆及衰减、恢复。
- `profile_presenter.py`：组装 `/api/profile` 响应。

`ProfileGraph` 本身只有一个节点，只是对普通画像服务的包装；Web 画像接口已经绕过该 Graph。

## 6. 数据模型和存储

项目主要使用 SQLite 和 JSON 文件。

### 教材数据

`sections.db` 的核心表是 `sections`，保存：

- 小节 ID、章名、小节标题；
- 起止页码；
- 教材正文；
- OCR 状态。

正式出题工具通过显式绑定的 `db_path` 查询正文、上下文和关键词，避免多个教材并发时共享错误的全局数据库路径。

### 学习数据

`attempts.db` 中的主要概念包括：

- `attempts`：题目、学生答案、正确性、题型、难度、章节、主题和时间；
- `attempt_error_labels`：错因、置信度、证据、建议及来源；
- `learning_sessions`：一次摸底或练习的开始、完成、目标和效果；
- `profile_snapshots`：练习前后的画像快照；
- `student_memory_facts`：长期薄弱点和恢复状态。

教材注册信息保存在 `cache/books.json`。生成试卷写入 `output/`，往年试卷分析写入 `analysis/`，上传原文件保存在 `uploads/`。

## 7. 关键算法

### BKT

每个规范化教材小节拥有独立的掌握概率。默认参数为：

- 初始掌握概率 `P(L0)=0.30`；
- 学习转移率 `P(T)=0.15`；
- 猜测概率 `P(G)=0.20`；
- 失误概率 `P(S)=0.10`。

每次作答先应用学习转移，再根据答对或答错执行贝叶斯更新，最后把概率限制在合理范围内。题目的自由文本 topic 只用于展示，小节 ID 才是画像身份，避免同一知识点因文案不同而被拆分。

### Bandit 推荐

推荐以“不掌握概率”为主要需求信号，并结合 session 效果、错因、趋势和长期记忆。练习模式可使用 Thompson Sampling 增加探索；画像展示和离线评测使用 Beta 分布均值，保证排序稳定。

### 质量门禁

单题首先经过确定性规则检查，包括字段完整性、题型/难度一致性、选择题选项、填空标记、媒体契约和 LaTeX 平衡；通过后才调用大模型做相关性、正确性、难度匹配和表达质量审核。

内容失败最多重新生成 3 次；审核调用的技术失败最多重试 2 次。两种失败预算在业务上相互独立。

## 8. 模型调用方式

归档时项目默认使用 DeepSeek，模型配置可通过环境变量覆盖。LLM 接入建立在 LangChain `ChatOpenAI` 上，并使用 DeepSeek 的 OpenAI-compatible `base_url`。

项目自行实现了结构化输出适配：在 Prompt 中加入 JSON 示例，提取直接 JSON 或代码块 JSON，再由 Pydantic 验证。该实现包含 fallback，但存在把技术解析失败转成结构上成立、语义上接近空结果的风险。

真正需要多轮工具调用的地方主要只有两个：主编规划和知识提取。五类题目生成、质量审核和大部分判题调用本质上都是一次结构化模型请求。

## 9. LangGraph 在项目中的实际作用

LangGraph 直接用于以下部分：

- 出题主图和单题生成子图；
- 主编及知识提取的工具调用循环；
- 按题目动态 fan-out；
- 通过 Annotated reducer 合并并发分支；
- 判题和画像的单节点包装。

项目没有配置 LangGraph checkpointer，也没有使用持久化恢复、人工中断、时间旅行或流式图事件。`JudgeGraph` 与 `ProfileGraph` 都只有一个节点，主要业务已经由普通 Python 实现。因此，LangGraph 在这里更多是一层流程表达，而不是系统的核心能力。

## 10. API 概览

主要接口包括：

- `GET /api/books`：教材列表；
- `POST /api/books/upload`：上传并解析教材；
- `GET /api/questions`：读取当前题目；
- `POST /api/generate`：开始出题；
- `GET /api/generate/jobs/{id}`：查询生成状态；
- `POST /api/submit-exam`：判题并记录；
- `POST /api/attempt-correction`：人工修正判题；
- `GET /api/profile`：画像和推荐；
- `POST /api/analyze-exam`：分析往年试卷；
- `GET /api/exams`：历史试卷；
- `GET /api/evals`：评测结果。

## 11. 评测体系

项目已经建立本地离线评测，覆盖：

- 出题格式、题型、难度、答案、解析、关键词、公式和媒体契约；
- 规则判题、LLM 判题、空答案、大小写、空白和 LaTeX 归一化；
- BKT 更新方向、薄弱章节排序和错因到题型的推荐映射。

归档时记录的 baseline：

| 指标 | 分数 |
|---|---:|
| 总分 | 90.48% |
| 出题质量 | 100.00% |
| 判题一致性 | 71.43% |
| 推荐策略 | 100.00% |

判题的两个失败样本来自关闭 LLM 时，规则无法识别主观题和综合题的语义等价。评测尚未接入 CI；默认基线不使用 LLM-as-judge，也不使用真实学生数据。

## 12. 运行环境与入口

- Python 3.11+；
- 项目 Python 依赖由 Conda 管理；历史评测环境记录为 `D:\conda\envs\book_exam`；
- DeepSeek API Key；
- 服务入口：`python web/server.py`；
- 默认地址：`http://localhost:8765`；
- 教材准备入口：`python parse.py ...`。

仓库没有发现完整、统一的 `requirements.txt`、`pyproject.toml` 或 Conda environment 文件，因此依赖环境无法只靠仓库内容可靠重建。

## 13. 归档时的真实状态

截至 2026-09-13，工作区不是干净状态：

- 27 个 tracked 文件存在未提交修改；
- 存在 `.tmp/`、两份评测日志和一个数据库迁移脚本等 untracked 内容；
- 改动集中在画像、推荐、判题、出题状态和 Web 服务，体现出项目正在从多学生概念收缩为单用户设计；
- 最近提交集中在画像推荐复用、质量门禁、审计记录和重试闭环；
- Git 远程仓库仍保存已推送/已提交历史，但不能恢复未提交改动、本地数据库、上传文件和输出结果。

本地目录约包含：

- `.git` 历史约 170 MiB；
- 上传文件约 108 MiB；
- 原始教材 PDF 约 36 MiB；
- 教材及学习数据库约 5 MiB；
- 临时文件、生成试卷、评测报告和分析结果；
- `.env`，可能包含 API 密钥，本文未读取或记录其值。

## 14. 项目的优点

- 产品闭环完整，不只是孤立的 Prompt 示例；
- 教材内容和学生历史都落在本地 SQLite，数据流容易追踪；
- 出题请求、题目计划、单题任务和题目草稿已经形成显式领域模型；
- 单题质量门禁同时包含确定性规则与语义审核；
- 技术失败和内容失败具有独立重试语义；
- 判题提供超时、并发限制和保守降级；
- BKT、Bandit、趋势、长期记忆和 session 效果形成了真实反馈闭环；
- 已有固定样本和离线 baseline，可用于重构回归。

## 15. 主要问题和未完成部分

- LangGraph 在判题和画像中只是单节点包装，增加了概念而没有增加能力；
- 主图状态依赖 reducer、消息清理和较高 recursion limit，业务含义被框架状态稀释；
- 自定义结构化输出 fallback 可能掩盖模型或解析故障；
- Web 后台任务只保存在内存，进程重启后不可恢复；
- 标准库 `HTTPServer`、后台线程、同步模型调用和内部 asyncio 混合，运行模型较复杂；
- 生成 fan-out 没有清晰的全局并发上限；
- 依赖清单缺失，环境复现困难；
- 离线评测尚未进入 CI，真实 LLM 回归成本和稳定性没有自动控制；
- 归档时存在大量未提交改动，最后状态未形成稳定版本。

## 16. 如果未来重新实现

可以保留产品和领域逻辑，但采用更直接的结构：

```text
Web/API
  → ExamService / JudgeService / ProfileService
  → 普通 Python 显式编排
  → 一个薄的 LLMClient
  → OpenAI Python SDK，以 provider adapter 接入 DeepSeek
  → SQLite repositories
```

建议保留的设计：

- `GenerationRequest → ExamPlan → QuestionTask → QuestionDraft`；
- 单题确定性门禁与语义审核；
- 内容重试和技术重试分离；
- 每本教材独立的数据目录；
- 规则判题优先、LLM 判题补充、故障保守降级；
- BKT、推荐、session 前后快照和离线评测。

建议不再保留的设计：

- 单节点 JudgeGraph 和 ProfileGraph；
- 为固定业务流程引入大范围图状态和 reducer；
- 解析失败后制造空的结构化结果；
- 把任务状态只放在 Web 进程内存；
- 模块级可变数据库上下文。

## 17. 删除与恢复说明

若归档完成后删除项目目录：

- Git 远程仓库只能恢复已经提交并推送的源代码；
- 当前未提交修改无法从远程恢复；
- `.env`、SQLite 数据库、上传文件、教材原件、生成试卷、评测日志和分析结果通常无法从 Git 恢复；
- 如果 `.git` 也被删除，本地提交、引用和 reflog 将一并消失；
- 本文档只保存项目含义和重建线索，不保存原始数据或完整源码。
