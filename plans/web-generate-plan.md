# 网页版出题 — 实现计划

## 目标

把 `python generate.py --mode xxx --student xxx` 搬到网页上。同时把出题、答题、画像三个页面合并成一个 `index.html`，顶部 Tab 切换。

## 全局最优审查结果（已纳入）

- `_build_toc_from_db` 不复制粘贴，抽到 `agent_utils.py`
- server.py handler 限 30 行以内

## 改动清单

### 0. `exam/agents/utils/agent_utils.py` — 搬 `_build_toc_from_db`

- 从 `generate.py` 搬过来，改名 `build_toc_from_db`（公开函数）
- `generate.py` 改为 import，删除本地定义

### 1. `web/server.py` — 加 `POST /api/generate` 端点

**新增 imports**：`ExamGraph`、`build_toc_from_db`

**新增 handler** `_handle_generate()`（≤30 行）：
- 接收 JSON：`{ mode, student_id, focus, count, types }`
- practice 模式校验 student_id 必填
- 调 `ExamGraph.propagate(...)`，debug=False（不打印进度）
- 出完后 `get_questions()` 重载 QUESTIONS
- 返回 `{ ok, count }` 或 `{ ok: false, error }`

**注册路由**：`do_POST` → `/api/generate`

### 2. 新建 `web/index.html` — 统一入口，Tab 式三合一

把出题、答题、画像合并到一个页面，顶部三个 Tab：

```
┌──────────────────────────────────┐
│  📝 出题  │  ✏️ 答题  │  📊 画像  │  ← Tab 栏（固定顶部）
├──────────────────────────────────┤
│                                  │
│        当前 Tab 的内容区          │
│                                  │
└──────────────────────────────────┘
```

**Tab 1 — 出题**（原 generate.html 功能）：
- 模式选择（全书/摸底/练习）
- 题数、题型、重点、学生 ID
- 生成按钮 + loading + 结果

**Tab 2 — 答题**（原 quiz.html 功能）：
- 完整迁移，逻辑不变
- 交卷后结果显示 + "查看画像 →" 改为自动切换到画像 Tab

**Tab 3 — 画像**（原 profile.html 功能）：
- 完整迁移，逻辑不变
- 页面加载时自动获取画像

**Tab 交互**：
- 答题 Tab 交卷成功后，"查看画像"按钮自动切到画像 Tab
- 出题 Tab 生成成功后，"去答题"按钮自动切到答题 Tab（题目已重载）
- 画像 Tab 从答题 Tab 切换过来时自动刷新（用可见性检测）

**CSS**：统一复用现有暖色调风格，Tab 栏用半透明毛玻璃效果固定在顶部

### 3. `web/quiz.html` + `web/profile.html` — 保留

- 保留但简化——每个页面顶部加一行导航指向 `index.html`
- 或者直接重定向到 `index.html` 对应 Tab

## 不做的

- 不出进度条/SSE：同步等待，和交卷体验一致
- 不改 ExamGraph 内部
- quiz.html / profile.html 不删，保留独立访问能力

## 验证

1. `python web/server.py` → 打开 `index.html`
2. 出题 Tab → 摸底诊断 → 生成 → 成功 → 点"去答题"
3. 自动切到答题 Tab → 加载新题目 → 答题 → 交卷
4. 点"查看画像" → 自动切到画像 Tab → BKT 数据正确
