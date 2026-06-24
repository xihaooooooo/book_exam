# 会话交接 — 2026-06-24

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
