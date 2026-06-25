# 会话交接 — 2026-06-25 (会话 4)

## 做了什么

### CLI 删除收口
- **删除 `generate.py` / `show_profile.py` / `record_attempt.py` / `show_db.py` / `main.py` / `analyze_exam.py` / `record_mistake.py`**：产品入口统一为 Web/API（`web/index.html` → `web/server.py`），不再保留 CLI 学习闭环入口。
- **保留 `parse.py`**：教材入库/数据准备工具，不属于学生学习闭环。
- `backfill_sessions.py`：标注为内部维护工具，不参与学习闭环。

### 网页三合一 + 出题/答题/画像/试卷分析一键化
- **新建 `web/index.html`**：Tab 式统一入口，三个 Tab（出题/答题/画像）无缝切换
- 出题 Tab：三种模式 exam/diagnostic/practice + focus 定向考点 + 往年试卷分析参照（上传 DOCX → LLM 自动解析）
- 交卷后自动切到画像 Tab，出题生成完自动切到答题 Tab——完整闭环
- **`POST /api/generate`**：网页端触发 ExamGraph 出题，出完自动重载 QUESTIONS
- **`POST /api/analyze-exam`**：上传 DOCX → base64 解码 → parse_docx → analyze_exam → generate_report → 自动入选下拉框
- **`GET /api/analysis-reports`**：列出 analysis/ 目录下可用报告

### 画像展示优化
- **章节标题补全**：`/api/profile` 自动从 `sections.db` 查标题，topic 为空时用教材章节名填充
- **LaTeX 清洗**：`$\mu \mathrm{C} / \mathrm{OS}-\mathrm{II}$` → 自动去标记，合并多余空格
- **错因中文映射**：`concept_confusion` → "概念混淆"，在 API 层做映射而非前端
- `build_toc_from_db` 从 `generate.py` 搬到 `agent_utils.py`，`generate.py` 和 `server.py` 共用

### 判题性能修复
- `DIAGNOSIS_CONCURRENCY` 从 2 → 5，选择题错因诊断速度翻倍

### 面试调研（四轮深度搜索）
- 搜了 AI Agent 八大核心能力：认知架构、记忆系统（Mem0/Zep/Letta 对比）、工具使用（MCP/A2A）、反思、护栏安全、Eval/可观测、多 Agent 编排、Harness 工程
- 搜了 2026 年市场：Agent 岗位同比 +455%、平均月薪 ¥60,738、技能红利窗口还剩 12-18 个月
- 搜了大厂招聘风向：阿里/字节/腾讯/京东的 Agent 人才需求、复合能力要求
- 评估了项目竞争力：多 Agent 编排+BKT 算法+Harness 工程是核心卖点；Eval/测试是最大硬伤
- 结论：项目有研究级技术深度（BKT+Thompson Sampling 和斯坦福 BEAGLE 框架一致），但缺 README+架构图+测试

### Git 提交
- `c522b4b`：BKT+Bandit 推荐引擎 + JudgeGraph LLM 判题 + 网页画像（19 files, +2653/-249）
- `539d759`：网页三合一 + 试卷分析上传 + 章节标题清洗（8 files, +1173/-34）

## 还没做

- **README + 架构图**：面试官 30 秒决定看不看，这个投入产出比最高
- **BKT 单元测试**：`test_bkt.py`，5 个测试场景（全对→0.999、全错→0.025、空序列→P(L₀)、交替→收敛、session 奖励）
- **出题质量评估**：pass rate / retry 率 / 难度偏差统计，加一个 `GET /api/stats` 端点
- **摸底→练习自动衔接**：diagnostic 交卷后自动提示"你的薄弱点在 X，要针对性练习吗？"
- **LangGraph 节点拆分**：strategy_router 太胖（180 行），拆成 profile_router → recommendation_planner
- **MCP 协议包装**：把 `search_keyword`、`get_section_text` 等 tool 包装成 MCP Server
- **记忆系统**：ProfileGraph 目前是实时聚合，没有跨会话记忆管理和遗忘曲线
- **护栏安全层**：预执行行为护栏（白名单/去重）+ 外部看门狗 + 审计日志

## 待决定

- 下一个优先级：README+架构图（面试准备） vs 摸底→练习自动衔接（产品体验） vs BKT 测试（代码质量）
- 项目面试定位：强调"多 Agent 架构 + 算法深度（BKT+Bandit） + Harness 工程"三张牌
- 是否需要录 demo 视频？

## 当前阻塞

- 无硬阻塞。代码跑通、闭环验证通过。

## 下一步建议

1. **README + 架构图**（最高投入产出比）：写出系统架构、BKT 公式、Agent 协作图，面试直接发
2. **`test_bkt.py`**（200 行）：补上最大的硬伤，面试官问到测试不会翻白眼
3. **出题质量统计**：`GET /api/stats` 返回 pass rate / retry 率 / 难度偏差，刷 Eval 这层
4. 以上三个做完，项目从"能跑"变成"能面"——可以开始投简历了

---

# 会话交接 — 2026-06-24 (会话 3)

## 做了什么

### JudgeGraph 端到端验证 + Python 3.13 事件循环 bug 修复
- 启动了 web server + demo 题交卷，确认 `attempt_error_labels` 表有 LLM 标签入库
- **发现并修复 bug**：`asyncio.get_event_loop()` 在 Python 3.13 下抛 RuntimeError，导致所有简答题降级为 fallback（"事件循环异常"）。改用 `asyncio.get_running_loop()` 后修复，简答判题从 6/6 全降级 → 6/6 正常 LLM，耗时从理论 60s → 实际 9s

### BKT + Bandit 推荐引擎（Phase 1 + Phase 2 全部完成）
- **全局最优审查** → 发现 5 个问题 → 更新计划（BKT 作为 ProfileGraph 后端不另起炉灶、Bandit 奖励用提升潜力而非 ΔP(L)、practice_sessions 表推迟到 Phase 2）
- **Phase 1 MVP 实现**（6 个文件）：
  - `schemas.py`：+BKTParams, BKTState, BanditState, RecommendationItem, RecommendationPlan（含 `to_prompt_table()`）
  - `profile_engine.py`：+`_load_topic_groups()` 公共函数、+`_bkt_replay()` 贝叶斯回放、`build_profile()` 加 `mastery_backend` 参数
  - `profile_graph.py`：消除重复代码，委托给 `build_profile()`，加 `mastery_backend` 字段
  - `recommendation.py`（新建）：Thompson Sampling（潜力先验 k=3）、难度/题型推荐、`build_recommendation_plan()`
  - `strategy.py`：practice 模式切到 BKT 后端 + 推荐引擎，回退兜底保留
  - `chief_editor.py`：practice prompt 改用 `plan.to_prompt_table()` 结构化表格
- **Phase 2 闭环**（不新增 DB 表）：
  - `profile_engine.py`：+`compute_session_rewards()` — 时间窗口切分 session（gap > 30min），算 ΔP(L) 奖励
  - `recommendation.py`：奖励用 `(1-P(L))` 缩放叠加到 Beta（防止 mastered topic 的冷启动 artifact）
  - `strategy.py`：调推荐引擎前自动算 session rewards

### 算法原理详解
- BKT 四参数：P(L₀)=0.30, P(T)=0.15, P(G)=0.20, P(S)=0.10
- Thompson Sampling：Beta(α,β) 潜力先验 + 采样排序 → 自动 explore/exploit
- Session 奖励：ΔP(L) per session，`effective = reward × (1-P(L))` 防假奖励

### 验证结果
- ✅ BKT P(L) 单调性：全对 → 0.999，全错 → 0.025，空序列 → P(L₀)
- ✅ Thompson Sampling 分布：Beta(10,1) mean=0.91, Beta(1,10) mean=0.09
- ✅ 推荐排序：弱 topic P(L)=4% 排第 1，easy 3 题
- ✅ Phase 2 闭环：mastered topic 不被假奖励干扰，弱 topic 排前
- ✅ 完整端到端：~~`generate.py --mode practice --student e2e_test_001`~~（CLI 已删除，历史记录）→ 15 道题一次通过质检
- ✅ 出题→答题→判题→再推荐的闭环全部走通

## 还没做

- ~~**`show_profile.py --bkt`**~~（CLI 已删除）：BKT P(L) 概率和 Beta 分布改为通过 Web `/api/profile` 查看
- **章节粒度归一化**：`1.1` vs `1.1.2` 分裂——chief_editor 出题用细粒度节编号，demo 题手写粗粒度，导致同一知识点分成两个 topic
- **对接真实教材跑完整场景**：用 `25年春嵌入式操作系统` docx 出题，验证"速通"核心假设
- **网页端画像展示**：画像还在 CLI 看
- **摸底→练习自动衔接**：未动
- **LangGraph 节点拆分**（已打标签）：strategy_router 太胖，后续可拆成 profile_router → recommendation_planner → chief_editor

## 待决定

- 章节粒度归一化方案：在 chief_editor 侧按章归一（`1.1.2` → `1.1`），还是让所有题目统一用细粒度？
- 下一个优先级：B 端展示（show_profile --bkt / web 画像）vs C 端验证（对接真实教材跑场景）

## 当前阻塞

- 无硬阻塞。代码写完、验证通过、闭环跑通。

## 下一步建议

1. ~~**`show_profile.py --bkt`**~~（CLI 已删除）：BKT P(L) 和 Beta 分布改为通过 Web `/api/profile` 查看
2. **章节粒度归一化**（中等，解决数据分裂实际问题）：不改的话每次出题越多 topic 越多，BKT 效果打折扣
3. **对接真实教材**（核心价值验证）：用嵌入式操作系统教材跑完整场景

---

# 会话交接 — 2026-06-24 (会话 2)

## 做了什么

### JudgeGraph 全量 LLM 判题 + 错因诊断（核心工作）

- **设计**：判题+错因诊断统一在 JudgeGraph 内，不另建 Agent。choice/fill_blank 保留文本规则判对错、答错时补 LLM 诊断；short_answer/comprehensive/code_fill 走 LLM 一次调同时判对错+诊错因
- **计划文件**：`plans/architecture/判题LLM统一+错因诊断计划.md`（v3，已实现）
- **审查报告**：`plans/architecture/红蓝审查报告-判题LLM统一方案.md`
- **代码已实现**（4 个文件）：
  - `exam/graph/judge_graph.py` — 完整重写（ErrorTypeEnum、JudgeResult schema、双 Semaphore 隔离、三层解析兜底、`_strip_label`/`_normalize` 增强、诊断 retry）
  - `exam/student_profile/storage.py` — `record_attempts_batch` 内联写 error_labels
  - `exam/agents/utils/structured.py` — `_make_example` bool/int/float 类型匹配示例值
  - `exam/agents/utils/agent_states.py` — JudgeState 删死字段 `llm_client`
- **验证状态**：import 测试通过、storage 集成测试通过，但**未跑端到端**（没启动 web server 用真实 LLM 交卷）

### Skill 改进

- **red-blue-review**：新增 Phase 4（审查完 → 更新计划 → 等确认 → 才写代码），明确审查对象是设计文档不是代码
- **global-optimum-review**：同样新增 Phase 4 和核心规则段
- **session-handoff**：改为保留最近 5 条，新增在上，旧记录下移

### 过程教训

- 红蓝审查完跳过了"更新计划"直接写代码，被纠正。正确流程：**计划 → 审查 → 更新计划 → 用户确认 → 代码**

## 还没做

- **端到端验证**：启动 `python web/server.py`，用 demo 题目答题交卷，确认 `attempt_error_labels` 表有 llm 标签
- ~~**practice 模式端到端**~~（CLI 已删除）：practice 端到端改为通过 Web `/api/generate` mode=practice 验证
- **错因诊断 Agent 阶段 5**：本次 JudgeGraph 改造实质上就是阶段 5。设计已定、代码已写，缺端到端验证
- **推荐引擎（阶段 3）**：未动
- **摸底→练习自动衔接**：未动
- **网页端画像展示**：未动

## 待决定

- 推荐引擎要不要做成独立 Agent？（上次遗留，未讨论）
- 速通系统第一个完整场景的 MVP 边界在哪？（上次遗留，未讨论）

## 当前阻塞

- 无硬阻塞。JudgeGraph 代码已写完并通过单元验证，等端到端测试

## 下一步建议

1. **端到端验证 JudgeGraph**：启动 web server，用 demo 题交卷，故意答错几道，查 DB 确认 error_labels 入库
2. 如果端到端通，阶段 5（错因诊断）就完成了，可以继续阶段 3（推荐引擎）或补 practice 端到端

---

# 会话交接 — 2026-06-24 (会话 1)

## 做了什么

- 确立了项目定位：**期末大学生速通系统**，不是通用考试工具
- 理清了 Agent 架构——ExamGraph / JudgeGraph / ProfileGraph 三个独立 Agent 通过 DB 协作
- 判题管道从"前端 substring 判定"重构为"后端批量交卷 → JudgeGraph 判定"，简答题 LLM 并发
- 新建设计审查工具：全局最优审查（global-optimum-review）和红蓝对抗（red-blue-review）
- 完成 Student Profile 阶段 1 收尾：schemas.py、attempt_error_labels 表、record_attempt.py CLI
- 完成阶段 2 画像聚合：ProfileGraph + show_profile.py
- practice 模式接入了 ProfileGraph，根据掌握等级 + 错因推导出题策略
- 新建会话交接 skill（session-handoff）
- 把架构图写入了 CLAUDE.md

## 还没做

- **错因诊断 Agent**（阶段 5）：LLM 自动判断错因，现在只能手动 CLI 打标签
- **推荐引擎**（阶段 3）：画像告诉你弱在哪，但不会说"练什么提分最快"
- **摸底→练习自动衔接**：diagnostic 跑完需要手动跑 practice
- **网页端画像展示**：画像只在 CLI 看，web 没有
- **practice 端到端没测过**：只单元测了 strategy_router，~~没真正跑 generate.py --mode practice~~（CLI 已删除，改为 Web 验证）
- **四种出题方式的 tool loop 风险**：chief_editor 靠 LLM 自觉调工具，prompt 可能抑制工具调用

## 待决定

- 推荐引擎要不要做成独立 Agent？
- 速通系统第一个完整场景的 MVP 边界在哪？

## 当前阻塞

- 无硬阻塞。需要决定下一阶段优先级

## 下一步建议

- ~~先跑一遍 `python generate.py --mode practice --student test_cli`~~（CLI 已删除）：改为 Web `/api/generate` mode=practice 验证
- 然后补阶段 3（推荐引擎）或阶段 5（错因诊断）
