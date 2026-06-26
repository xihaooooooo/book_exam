# BKT + Bandit 推荐引擎实现计划

> 已通过全局最优审查，以下为修正后的最终方案。

## Context

当前 `--mode practice` 的出题策略是简单映射：ProfileGraph 给出 weak/unstable 知识点 → 拼成逗号分隔的 section_id → chief_editor 按 "70% weak / 30% 扫一遍" 出题。问题：

- **掌握评估用硬阈值**（≥85% + ≥5次 → mastered）
- **无优先级排序**：3 个 weak topic 一视同仁
- **无难度递进**：practice_plan 算了 difficulty 但下游不用
- **无闭环**：每次 practice 从零开始，不利用上次练习结果
- **无 explore/exploit 平衡**：只练已知薄弱点，不会探测"伪掌握"

## 核心设计

### 算法分工

```
BKT:   每个知识点掌握的概率 P(L) 是多少？ → 状态估计器（observer）
Bandit: 下一道题练哪个知识点收益最大？   → 决策器（policy）

BKT → P(L) per topic → Bandit Beta 先验 → Thompson Sampling 排序 → 出题
Bandit ← ΔP(L) 反馈 ← 学生答题结果 ← JudgeGraph
```

### BKT 作为 ProfileGraph 的替代掌握评估后端

**关键决策（审查发现 #1, #2）**：BKT 不绕过 ProfileGraph 另起炉灶，而是作为 `profile_engine.py` 的一个替代 mastery 后端。`build_profile()` 增加 `mastery_backend` 参数：

```python
# 现有
profile = build_profile("S001", "cache/attempts.db")  # 硬阈值

# 新增
profile = build_profile("S001", "cache/attempts.db", mastery_backend="bkt")  # BKT
```

这样避免了"双画像"问题——`show_profile.py` 可以加 `--bkt` 参数显示 BKT P(L) 值，和 practice 模式看到的是同一个东西。

### BKT 公式

四个参数：P(L₀)=0.30, P(T)=0.15, P(G)=0.20, P(S)=0.10（文献默认值，后续可选 EM 拟合）

```
每次答题前（学习转移）：
  P(L_n) = P(L_{n-1}) + (1 - P(L_{n-1})) × P(T)

答对后（贝叶斯更新）：
  P(correct) = P(L) × (1-P(S)) + (1-P(L)) × P(G)
  P(L|correct) = P(L) × (1-P(S)) / P(correct)

答错后：
  P(wrong) = P(L) × P(S) + (1-P(L)) × (1-P(G))
  P(L|wrong) = P(L) × P(S) / P(wrong)

限制在 [0.001, 0.999] 防止数值坍缩
```

### Thompson Sampling + 奖励信号统一

**Phase 1 先验**：基于提升潜力 `potential = 1 - P(L)`，确保弱 topic 自然排前面。
**Phase 2 闭环**：在 prior 上叠加实际 ΔP(L) 观测，两者语义一致（都是"练一次有多大概率提升"）。

```
Phase 1 先验（提升潜力）：
  potential = max(0.01, 1 - P(L))
  α = 1 + k × potential    (k=3 控制先验强度)
  β = 1 + k × P(L)
  → Beta 均值 ≈ potential / (potential + P(L)) = 1 - P(L)
  → P(L)=0.04 → mean≈0.74, P(L)=0.93 → mean≈0.07

Phase 2 闭环（叠加实际奖励）：
  reward = max(0, P(L)_post - P(L)_pre)
  α += reward, β += (1 - reward)
  → 语义完全一致，只是粒度从"潜力先验"叠加"实际观测"

每轮 Thompson Sampling：
  对所有 topic 采样 θ ~ Beta(α, β)，按 θ 降序排列
```

依赖：仅用 Python stdlib `random.betavariate(α, β)`，零外部依赖。

## 实施计划

### Phase 1 — MVP（推荐引擎替代简单策略）

**新建文件**：
- `exam/student_profile/recommendation.py` — Thompson Sampling + 推荐计划构建逻辑（Bandit 相关）

**修改文件**：
- `exam/student_profile/profile_engine.py` — 加 `bkt_replay()` 函数、`build_profile()` 加 `mastery_backend` 参数、抽取公共的 `_load_topic_groups()` 供 BKT 和阈值共用
- `exam/student_profile/schemas.py` — 加 BKTParams、BKTState、BanditState、RecommendationItem、RecommendationPlan；RecommendationPlan 加 `to_prompt_table()` 方法
- `exam/graph/strategy.py` — practice 模式调用 `build_profile(mastery_backend="bkt")` 获取 P(L)，再调 `recommendation.py` 做 Thompson Sampling 排序和推荐计划构建
- `exam/agents/planner/chief_editor.py` — practice 模式 prompt 改用 `plan.to_prompt_table()` 生成结构化表格

**Phase 1 不改的文件**（推迟到 Phase 2）：
- `exam/student_profile/storage.py` — practice_sessions 表推迟到 Phase 2
- `exam/graph/exam_graph.py` — 同上，Phase 2 再加 session 记录

**MVP 行为变化**：

| 维度 | 现在 | MVP 后 |
|------|------|--------|
| 掌握评估 | ProfileGraph 硬阈值 | ProfileGraph + BKT 后端（通过 `mastery_backend="bkt"`） |
| topic 排序 | 按 mastery_level 分组 | 按 Thompson 采样值降序 |
| 题数分配 | weak×2 + familiar | Bandit 分数高 → 多出 |
| 难度 | 固定 easy_to_medium | 按 P(L) 阶梯：<0.3→easy, 0.3-0.5→easy_to_medium, 0.5-0.7→medium |
| chief_editor 输入 | `focus="3.1,5.2,6.1"` | `plan.to_prompt_table()` 结构化表格 |
| 闭环 | 无 | Bandit 奖励基于 P(L) - P(L₀)，后续平滑过渡到 per-session |

**新学生无数据时**：BKT 没有可回放的 attempts，回退 ProfileGraph 阈值模式（行为不变）。

### Phase 2 — 闭环优化（等 MVP 跑通后）

- `storage.py` — 加 `practice_sessions` 表 + `init_practice_sessions_db()` + `record_practice_session()`
- `exam_graph.py` — practice 结束后记录 session snapshot
- `recommendation.py` — 加 `update_bandit_from_session()`，从 session 的 ΔP(L) 更新 Beta(α, β)
- BKT 参数调优：按题型分别设 P(G)/P(S)（选择题猜对概率高，简答题低）
- `show_profile.py` — 加 `--bkt` 参数显示 BKT P(L)

### Phase 3 — 高级特性（远期）

- 难度感知 BKT：easy/medium/hard 各有独立 P(T)
- 时间衰减 Bandit：长时间没练的 topic 的 α/β 衰减（遗忘曲线）
- 多目标优化：薄弱修复 vs 考试覆盖的 Pareto 前沿
- BKT 状态持久化表（性能优化）

## 关键数据结构

```python
# ── BKT（profile_engine.py）──

@dataclass
class BKTParams:
    p_L0: float = 0.30     # 初始掌握概率
    p_T: float = 0.15      # 学习转移率
    p_G: float = 0.20      # 猜测概率
    p_S: float = 0.10      # 失误概率

@dataclass
class BKTState:
    section_id: str
    topic: str
    p_mastery: float       # 当前 P(L)
    total_attempts: int
    correct_count: int
    params: BKTParams

# ── Bandit（recommendation.py）──

@dataclass
class BanditState:
    section_id: str
    alpha: float = 1.0     # Beta 分布 α（累积 ΔP(L) reward）
    beta: float = 1.0      # Beta 分布 β（累积 1-reward）

@dataclass
class RecommendationItem:
    section_id: str
    topic: str
    p_mastery: float          # BKT P(L)
    bandit_score: float       # Thompson 采样值
    difficulty: str            # 推荐起始难度
    question_types: list[str] # 推荐题型
    recommended_count: int    # 建议题数

@dataclass
class RecommendationPlan:
    student_id: str
    items: list[RecommendationItem]  # 按 bandit_score 降序
    target_count: int
    reason: str

    def to_prompt_table(self) -> str:
        """格式化为 chief_editor prompt 中的 Markdown 表格。"""
        rows = ["| 优先级 | 章节 | 掌握度 P(L) | 主要错因 | 建议题型 | 建议难度 | 建议题数 |",
                "|--------|------|------------|----------|----------|----------|----------|"]
        for i, item in enumerate(self.items, 1):
            rows.append(
                f"| {i} | {item.section_id} {item.topic} | {item.p_mastery:.0%} | "
                f"{getattr(item, 'dominant_error_type', '') or '-'} | "
                f"{','.join(item.question_types)} | {item.difficulty} | {item.recommended_count} |"
            )
        return "\n".join(rows)
```

## 数据流

```
ProfileGraph.build_profile(mastery_backend="bkt")
  │
  ├─ _load_topic_groups(db, student_id)   ← 公共函数，BKT 和阈值共用
  ├─ bkt_replay(attempts, params) → BKTState per topic
  └─ 其余字段（error_distribution, risk_signals）不变
      │
      ▼
strategy_router（practice 模式）
  │
  ├─ 取 profile.topics，每个 topic 有 BKTState.p_mastery
  ├─ 调 recommendation.build_plan(topics, target_count)
  │   ├─ 初始化 Bandit: α = 1 + r×N, β = 1 + (1-r)×N   (r = P(L) - P(L₀))
  │   ├─ Thompson Sampling 排序
  │   └─ 按 P(L) + 错因 推难度/题型/题数
  └─ 产出 RecommendationPlan
      │
      ▼
chief_editor
  │
  └─ plan.to_prompt_table() → 嵌入 prompt
```

## 验证方法

1. **BKT 单元验证**：构造已知答题序列 → 断言 P(L) 单调性（全对→逼近 1，全错→逼近 P(G) floor）→ 断言不出现 NaN
2. **Thompson Sampling 分布验证**：Beta(10,1) 采样均值 ~0.91, Beta(1,10) ~0.09（万次采样）
3. **reward 语义一致性**：全局 ΔP(L) 奖励和 per-session ΔP(L) 奖励量纲相同，验证 Phase 1→2 过渡时 Beta 参数不需要重新初始化
4. **集成测试**：insert 合成 attempt 数据（3 topic：weak/medium/mastered）→ `build_profile(mastery_backend="bkt")` → 断言 P(L) 值合理 → `recommend()` → 断言弱 topic 排第一
5. **端到端**：`python generate.py --mode practice --student e2e_test_001` → 检查 focus 按 bandit 排序
6. **回归**：exam 模式不变；diagnostic 模式不变；无数据学生回退阈值模式
7. **show_profile 兼容**：`python show_profile.py --student S001` 默认行为不变（阈值），加 `--bkt` 后显示 P(L)
