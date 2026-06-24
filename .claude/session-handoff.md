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
- ✅ 完整端到端：`generate.py --mode practice --student e2e_test_001` → 15 道题一次通过质检
- ✅ 出题→答题→判题→再推荐的闭环全部走通

## 还没做

- **`show_profile.py --bkt`**：加参数显示 BKT P(L) 概率和 Beta 分布，现在 BKT 只在 practice 内部跑
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

1. **`show_profile.py --bkt`**（小改动，马上能用）：加参数显示 P(L) 和 Beta 分布，让 BKT 对人类可见
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
- **practice 模式端到端**（上次交接遗留）：`python generate.py --mode practice --student <id>`
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
- **practice 端到端没测过**：只单元测了 strategy_router，没真正跑 generate.py --mode practice
- **四种出题方式的 tool loop 风险**：chief_editor 靠 LLM 自觉调工具，prompt 可能抑制工具调用

## 待决定

- 推荐引擎要不要做成独立 Agent？
- 速通系统第一个完整场景的 MVP 边界在哪？

## 当前阻塞

- 无硬阻塞。需要决定下一阶段优先级

## 下一步建议

- 先跑一遍 `python generate.py --mode practice --student test_cli` 确认端到端通
- 然后补阶段 3（推荐引擎）或阶段 5（错因诊断）
