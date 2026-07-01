# L1：LaTeX 公式支持 — 详细实施计划

## 目标

让系统能够出、答、判包含数学公式的题目。前端用 KaTeX 渲染 `$...$` 和 `$$...$$`，后端不改架构，只调 prompt 和字符串处理。

## 改什么（5 处）

### 1. chief_editor prompt — 要求 LLM 用 LaTeX 写公式

**文件**：`exam/agents/planner/chief_editor.py`

现有 prompt 末尾追加两行：

```
- 数学表达式和公式必须使用 LaTeX 语法
- 内联公式用 $...$ 包裹，单独成行的公式用 $$...$$ 包裹
```

**影响**：LLM 生成的题目里，`stem`、`options`、`explanation`、`correct_answer` 都会自然带 LaTeX。

**风险**：LLM 可能生成错误 LaTeX（比如 `$(a+b)^2$` 写成 `(a+b)^2` 忘记加 `$`）。这个靠质检节点兜底——quality_reviewer prompt 里加一句"检查数学公式是否用 $...$ 包裹"。

---

### 2. question_generator.py — 保持透传

**文件**：`exam/agents/generators/question_generator.py`

这层不改。LaTeX 只是文本的一部分，生成器不需要知道它的存在。

---

### 3. 判题 prompt — 忽略 LaTeX 空格差异

**文件**：`exam/graph/judge_graph.py`

判断简答题/综合题时，LLM 比对 prompt 里已有的"忽略格式差异"说明加一条：

```
- LaTeX 公式中多余的空白字符不影响判定：$(a+b)^2$ 等价于 $(a + b)^2$
- 数学上等价但写法不同的 LaTeX 表达式视为相同：$\frac{1}{2}$ 等价于 $0.5$
```

文本规则判题（choice/fill_blank）：`student_answer.strip().upper()` 和 `correct_answer.strip().upper()` 比较时先去掉 `$...$` 包裹，再比较。LaTeX 不会影响选择题的字母匹配（选项是 A/B/C/D），但填空题可能比较数学表达式——加一行 `_strip_latex_delimiters()`：

```python
def _strip_latex_delimiters(s: str) -> str:
    """Remove $ and $$ wrappers to normalize LaTeX answers."""
    s = s.strip()
    if s.startswith("$$") and s.endswith("$$"):
        s = s[2:-2]
    elif s.startswith("$") and s.endswith("$"):
        s = s[1:-1]
    return s.strip()
```

填空判题时先 `_strip_latex_delimiters`，再做 `==` 比较。

---

### 4. 前端 — 引入 KaTeX

**文件**：`web/index.html`

`<head>` 内加两行：

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css"
      integrity="sha384-wcIxkf4kTLkViI2xGyCp7uN5kYP4YPmhVVGqxuBxZqT6D0BUiN3kCZQq8WjBJ4D"
      crossorigin="anonymous">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js"
        integrity="sha384-hIoBPJpTqYfviMyOGE4J2BFP1wB4v7xZbILvY1BjrpeBnWYfHHPk8LsMbrPBxij"
        crossorigin="anonymous"></script>
```

`<script>` 区域加渲染函数，**全局挂载到一个函数里，在任何渲染文本的地方调用**：

```javascript
function renderLatex(text) {
  if (!text || typeof text !== 'string') return text;
  // 先渲染块级 $$...$$，再渲染内联 $...$
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => {
    try { return katex.renderToString(tex.trim(), {displayMode: true, throwOnError: false}); }
    catch(e) { return _; }
  });
  text = text.replace(/\$([^\$]+?)\$/g, (_, tex) => {
    try { return katex.renderToString(tex.trim(), {throwOnError: false}); }
    catch(e) { return _; }
  });
  return text;
}
```

所有渲染点（stem、options、explanation、correct_answer、reason、profile 章节标题）在 `innerHTML` 赋值前过 `renderLatex()`。

**关键**：`throwOnError: false`——LLM 生成的 LaTeX 可能有语法错，不能因为一个 `$` 没配对就炸了整个页面。

---

### 5. 画像 — 标题出两份

**文件**：`exam/student_profile/profile_presenter.py`

`_load_section_titles` 目前返回 `{section_id: title_cleaned}`。

**改后**返回两个 dict：

```python
def _load_section_titles(sections_db: str):
    """返回 (titles_plain, titles_rich)
    titles_plain: 清洗 LaTeX 后的纯文本，画像页用
    titles_rich: 保留原始 LaTeX，答题区 KaTeX 渲染用
    """
    ...
    return titles_plain, titles_rich
```

`_build_topics_json` 里，`display_title` 仍然用 `titles_plain`（画像页）；新增可选字段 `topic_rich` 用 `titles_rich`（留给前端）。

`/api/profile` 响应里每个 topic 加 `topic_rich` 字段。

---

## 验收清单

### 验收 1：LLM 能出带公式的题

```
操作：POST /api/generate mode=exam count=3 types=choice focus="勾股定理"
检查：生成的题目 stem/options/explanation 里是否包含 $...$ 包裹的公式
```

### 验收 2：前端渲染公式

```
操作：在浏览器打开 index.html → 答题 Tab
检查：$a^2 + b^2 = c^2$ 显示为渲染后的数学符号，不是原始 LaTeX 文本
```

### 验收 3：选择题判题不受 LaTeX 影响

```
操作：生成带 LaTeX 公式的选择题，选正确答案 → 交卷
检查：判对（method=rule），不因 stem 含 $ 而误判
```

### 验收 4：填空题判题归一化

```
操作：标准答案是 $x = 5$，学生填 $x=5$（少空格）→ 交卷
检查：判对，"忽略 LaTeX 空格差异"生效
```

### 验收 5：KaTeX 报错不炸页面

```
操作：手动改一道题的 stem 为 "$a^2 + b^2"（故意少一个 $）→ 刷新页面
检查：页面正常显示原始文本（或只渲染了部分），不白屏，控制台无 red crash
```

### 验收 6：画像页不崩

```
操作：GET /api/profile
检查：topic 字段正常显示纯文本标题，"topic_rich" 字段存在且含原始 LaTeX
```

---

## 不变的东西

- BKT / Bandit 算法
- 三个 Agent 架构
- 数据库 schema（`attempts` 表字段不动）
- API 端点签名
- Session 闭环逻辑

---

## 预估工作量

| 文件 | 改动量 | 时间 |
|---|---|---|
| chief_editor.py | 2 行 prompt | 5 min |
| judge_graph.py | _strip_latex_delimiters + 2 行 prompt | 15 min |
| index.html | CDN 引用 + renderLatex 函数 + 调通所有渲染点 | 30 min |
| profile_presenter.py | _load_section_titles 改返回值 | 15 min |
| 端到端验证 | 验收 1-6 | 30 min |
| **合计** | | **~1.5 小时** |
