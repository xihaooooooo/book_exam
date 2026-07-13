const STUDENT_ID = "default";
let BOOKS = [];
let CURRENT_BOOK_ID = (() => {
  try { return localStorage.getItem('current_book_id') || ''; } catch(e) { return ''; }
})();
let CURRENT_SESSION_ID = (() => {
  try { const v = sessionStorage.getItem('current_session_id'); return v ? parseInt(v) : null; } catch(e) { return null; }
})();
const TYPE_LABELS = { choice:'选择题', fill_blank:'填空题', short_answer:'简答题', code_fill:'代码填空题', comprehensive:'综合题' };
const DIFF_LABELS = { easy:'简单', medium:'中等', hard:'困难', easy_to_medium:'简单→中等', medium_to_hard:'中等→困难' };
const ML_LABELS = { mastered:'已掌握', familiar:'熟悉', unstable:'不稳定', weak:'薄弱', unknown:'未知' };
const ERROR_TYPE_LABELS = {
  concept_confusion: '概念混淆',
  memory_gap: '记忆缺失',
  reasoning_error: '推理错误',
  misread_question: '审题错误',
  careless: '粗心失误',
  transfer_failure: '迁移失败',
};

let genMode = 'exam';
const typeMap = { '选择题': 'choice', '填空题': 'fill_blank', '简答题': 'short_answer', '代码填空题': 'code_fill', '综合题': 'comprehensive' };

let questions = [], qIdx = 0, qStartTs = Date.now(), confidence = 3, answers = [];

// ═══════════════════════════════════
// L1: LaTeX rendering & safe DOM helpers
// ═══════════════════════════════════
function renderLatex(text) {
  if (!text || typeof text !== 'string') return text;
  try {
    text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => {
      try { return katex.renderToString(tex.trim(), {displayMode: true, throwOnError: false}); }
      catch(e) { return _; }
    });
    text = text.replace(/\$([^\$]+?)\$/g, (_, tex) => {
      try { return katex.renderToString(tex.trim(), {throwOnError: false}); }
      catch(e) { return _; }
    });
  } catch(e) {}
  return text;
}

function safeSetHTML(el, text) {
  if (el) el.innerHTML = renderLatex(text);
}

function attr(s) {
  return esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function stripMediaMarkers(text) {
  return String(text || '').replace(/\s*\[media:[^\]]+\]\s*/g, ' ').replace(/\s{2,}/g, ' ').trim();
}

function mediaList(media) {
  return Array.isArray(media) ? media : [];
}

function mediaUrl(item) {
  const src = item && item.src ? String(item.src) : '';
  if (!src) return '';
  const params = new URLSearchParams();
  params.set('src', src);
  if (CURRENT_BOOK_ID) params.set('book_id', CURRENT_BOOK_ID);
  return '/api/media?' + params.toString();
}

function encodeMermaidSource(content) {
  try { return encodeURIComponent(String(content || '')); }
  catch(e) { return ''; }
}

function decodeMermaidSource(content) {
  try { return decodeURIComponent(String(content || '')); }
  catch(e) { return ''; }
}

function openImageViewer(src, title) {
  if (!src) return;
  closeImageViewer();
  const overlay = document.createElement('div');
  overlay.className = 'media-viewer';
  overlay.innerHTML = `<div class="media-viewer-panel">
    <button type="button" class="media-viewer-close" aria-label="关闭">×</button>
    <img src="${attr(src)}" alt="${attr(title || '题图')}">
    ${title ? `<div class="media-viewer-caption">${esc(title)}</div>` : ''}
  </div>`;
  document.body.appendChild(overlay);
}

function closeImageViewer() {
  const viewer = document.querySelector('.media-viewer');
  if (viewer) viewer.remove();
}

function canvasConfig(item) {
  return item && item.config && typeof item.config === 'object' ? item.config : {};
}

function canvasInitialState(config) {
  const state = config && config.initial_state && typeof config.initial_state === 'object'
    ? config.initial_state
    : config || {};
  return state && typeof state === 'object' ? state : {};
}

function normalizeCanvasNodes(state) {
  const raw = Array.isArray(state.nodes) ? state.nodes : [];
  return raw.map((node, index) => {
    if (node && typeof node === 'object') {
      const id = String(node.id || node.key || index);
      return {
        id,
        label: String(node.label || node.name || id),
        x: Number.isFinite(Number(node.x)) ? Number(node.x) : null,
        y: Number.isFinite(Number(node.y)) ? Number(node.y) : null,
      };
    }
    return { id: String(index), label: String(node), x: null, y: null };
  });
}

function normalizeCanvasEdges(state) {
  const raw = Array.isArray(state.edges) ? state.edges : [];
  return raw.map(edge => {
    if (edge && typeof edge === 'object') {
      return {
        from: String(edge.from || edge.source || ''),
        to: String(edge.to || edge.target || ''),
        label: edge.label ? String(edge.label) : '',
      };
    }
    if (Array.isArray(edge) && edge.length >= 2) {
      return { from: String(edge[0]), to: String(edge[1]), label: '' };
    }
    return null;
  }).filter(Boolean);
}

function layoutCanvasNodes(nodes, width, height) {
  if (!nodes.length) return [];
  const cx = width / 2, cy = height / 2;
  const radius = Math.min(width, height) * 0.34;
  return nodes.map((node, index) => {
    if (node.x !== null && node.y !== null) return node;
    const angle = nodes.length === 1 ? -Math.PI / 2 : (Math.PI * 2 * index / nodes.length) - Math.PI / 2;
    return {
      ...node,
      x: Math.round(cx + Math.cos(angle) * radius),
      y: Math.round(cy + Math.sin(angle) * radius),
    };
  });
}

function renderCanvasPreview(item) {
  const config = canvasConfig(item);
  const state = canvasInitialState(config);
  const canvasType = String(config.canvas_type || state.canvas_type || 'structure');
  const nodes = layoutCanvasNodes(normalizeCanvasNodes(state), 520, 260);
  const edges = normalizeCanvasEdges(state);
  const nodeById = Object.fromEntries(nodes.map(node => [node.id, node]));
  if (nodes.length) {
    const edgeHtml = edges.map(edge => {
      const from = nodeById[edge.from], to = nodeById[edge.to];
      if (!from || !to) return '';
      const mx = Math.round((from.x + to.x) / 2);
      const my = Math.round((from.y + to.y) / 2);
      return `<g class="canvas-edge">
        <line x1="${attr(from.x)}" y1="${attr(from.y)}" x2="${attr(to.x)}" y2="${attr(to.y)}"></line>
        ${edge.label ? `<text x="${attr(mx)}" y="${attr(my - 5)}">${esc(edge.label)}</text>` : ''}
      </g>`;
    }).join('');
    const nodeHtml = nodes.map(node => `<g class="canvas-node" transform="translate(${attr(node.x)},${attr(node.y)})">
      <circle r="28"></circle>
      <text text-anchor="middle" dominant-baseline="middle">${esc(node.label)}</text>
    </g>`).join('');
    return `<div class="canvas-preview" data-canvas-type="${attr(canvasType)}">
      <div class="canvas-preview-head">${esc(canvasType)}</div>
      <svg class="canvas-svg" viewBox="0 0 520 260" role="img" aria-label="${attr(item.description || canvasType)}">
        <defs>
          <marker id="canvasArrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
            <path d="M0,0 L0,6 L9,3 z"></path>
          </marker>
        </defs>
        ${edgeHtml}${nodeHtml}
      </svg>
    </div>`;
  }
  return `<div class="canvas-preview canvas-preview-empty">
    <div class="canvas-preview-head">${esc(canvasType)}</div>
    <pre>${esc(JSON.stringify(state || {}, null, 2)).slice(0, 1000)}</pre>
  </div>`;
}

function initMermaidRenderer() {
  if (!window.mermaid || window.__MERMAID_READY__) return !!window.mermaid;
  window.mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    theme: 'base',
    themeVariables: {
      primaryColor: '#F7EFE3',
      primaryTextColor: '#211B16',
      primaryBorderColor: '#C49A5E',
      lineColor: '#7A6B5A',
      fontFamily: 'Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    },
  });
  window.__MERMAID_READY__ = true;
  return true;
}

async function renderMermaidBlocks(root) {
  const scope = root || document;
  const blocks = Array.from(scope.querySelectorAll('.mermaid-diagram:not([data-rendered="1"])'));
  if (!blocks.length) return;
  if (!initMermaidRenderer()) {
    blocks.forEach(block => {
      block.textContent = '图表渲染器未加载';
      block.classList.add('mermaid-error');
      block.dataset.rendered = '1';
    });
    return;
  }
  for (const [index, block] of blocks.entries()) {
    const source = decodeMermaidSource(block.dataset.mermaidSource || '').trim();
    if (!source) continue;
    try {
      const id = `mmd_${Date.now()}_${index}_${Math.random().toString(36).slice(2)}`;
      const rendered = await window.mermaid.render(id, source);
      block.innerHTML = rendered.svg || '';
      block.dataset.rendered = '1';
    } catch(e) {
      block.textContent = '图表渲染失败';
      block.classList.add('mermaid-error');
      block.dataset.rendered = '1';
    }
  }
}

function renderMediaList(media) {
  const items = mediaList(media);
  if (!items.length) return '';
  const html = items.map(item => {
    if (!item) return '';
    const caption = item.description ? `<figcaption>${esc(item.description)}</figcaption>` : '';
    if (item.type === 'image') {
      const url = mediaUrl(item);
      if (!url) return '';
      const title = item.description || item.id || '教材插图';
      return `<figure class="media-figure" data-media-id="${attr(item.id || '')}">
        <img class="media-image" src="${attr(url)}" alt="${attr(item.id || '教材插图')}" loading="lazy">
        <button type="button" class="media-zoom" data-src="${attr(url)}" data-title="${attr(title)}">查看大图</button>
        ${caption}
      </figure>`;
    }
    if (item.type === 'mermaid') {
      const source = String(item.content || '').trim();
      if (!source) return '';
      return `<figure class="media-figure media-mermaid" data-media-id="${attr(item.id || '')}">
        <div class="mermaid-diagram" data-mermaid-source="${attr(encodeMermaidSource(source))}"></div>
        ${caption}
      </figure>`;
    }
    if (item.type === 'canvas') {
      return `<figure class="media-figure media-canvas" data-media-id="${attr(item.id || '')}">
        ${renderCanvasPreview(item)}
        ${caption}
      </figure>`;
    }
    return '';
  }).join('');
  return html ? `<div class="media-list">${html}</div>` : '';
}

function labelOf(i) { return String.fromCharCode(65+i); }
function fmtPct(v) { return Math.round((v||0)*100)+'%'; }
function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function domKey(s) { return Array.from(String(s||'')).map(c => c.charCodeAt(0).toString(36)).join('_'); }
function pLColor(p) { if(p<0.3)return'#B55A4A'; if(p<0.5)return'#D4956A'; if(p<0.7)return'#C49A5E'; if(p<0.85)return'#8AAA6A'; return'#4A7C59'; }
function bookParam() { return CURRENT_BOOK_ID ? '?book_id=' + encodeURIComponent(CURRENT_BOOK_ID) : ''; }
function withBookPayload(payload) { return Object.assign({ book_id: CURRENT_BOOK_ID }, payload || {}); }
function clearBookScopedState() {
  questions = []; answers = []; qIdx = 0; qStartTs = Date.now(); confidence = 3;
  CURRENT_SESSION_ID = null;
  try { sessionStorage.removeItem('current_session_id'); } catch(e) {}
  const quizRoot = document.getElementById('quizRoot');
  if (quizRoot) quizRoot.innerHTML = '';
  const profileRoot = document.getElementById('profileRoot');
  if (profileRoot) profileRoot.innerHTML = '';
}

// ═══════════════════════════════════
// Beta PDF canvas
// ═══════════════════════════════════
function drawBeta(canvasId, alpha, beta) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const N = 80, pts = []; let maxY = 0;
  for (let i = 0; i <= N; i++) {
    const x = 0.01 + i/N*0.98, y = betaPDF(x, alpha, beta);
    pts.push({x,y}); if (y>maxY) maxY=y;
  }
  const pad=2, plotW=W-pad*2, plotH=H-pad*2;
  if (maxY===0||!isFinite(maxY)) return;
  ctx.fillStyle='rgba(184,137,79,0.15)'; ctx.beginPath(); ctx.moveTo(pad,H-pad);
  for (const pt of pts) ctx.lineTo(pad+pt.x*plotW, H-pad-(pt.y/maxY)*plotH);
  ctx.lineTo(W-pad,H-pad); ctx.closePath(); ctx.fill();
  ctx.strokeStyle='#B8894F'; ctx.lineWidth=1.2; ctx.beginPath();
  let first=true;
  for (const pt of pts) { const sx=pad+pt.x*plotW, sy=H-pad-(pt.y/maxY)*plotH; if(first){ctx.moveTo(sx,sy);first=false;} else ctx.lineTo(sx,sy); }
  ctx.stroke();
  const mean=alpha/(alpha+beta), mx=pad+mean*plotW;
  ctx.strokeStyle='rgba(181,90,74,0.4)'; ctx.lineWidth=1; ctx.setLineDash([2,2]);
  ctx.beginPath(); ctx.moveTo(mx,pad); ctx.lineTo(mx,H-pad); ctx.stroke(); ctx.setLineDash([]);
}
function betaPDF(x,a,b){ return Math.exp((a-1)*Math.log(x)+(b-1)*Math.log(1-x)-lbeta(a,b)); }
function lbeta(a,b){ return lgamma(a)+lgamma(b)-lgamma(a+b); }
function lgamma(x){
  if(x<0.5) return Math.log(Math.PI/Math.sin(Math.PI*x))-lgamma(1-x);
  x-=1; return 0.5*Math.log(2*Math.PI)+(x+0.5)*Math.log(x+5.5)-(x+5.5)+Math.log(1+1/12/(x+5.5)+1/288/(x+5.5)/(x+5.5));
}

// ═══════════════════════════════════
// Book switcher
// ═══════════════════════════════════
let bookIdEdited = false;

function makeBookId(title) {
  let base = String(title || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
  if (!base) base = 'book-' + Date.now().toString(36);
  return base.slice(0, 48);
}

async function loadBooks(preferredId = '') {
  const res = await fetch('/api/books');
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || '书籍列表读取失败');
  BOOKS = data.books || [];
  const ids = BOOKS.map(b => b.book_id);
  let nextId = preferredId || CURRENT_BOOK_ID || data.default_book_id || (BOOKS[0] && BOOKS[0].book_id) || '';
  if (nextId && !ids.includes(nextId)) nextId = data.default_book_id || ids[0] || '';
  CURRENT_BOOK_ID = nextId;
  try { if (CURRENT_BOOK_ID) localStorage.setItem('current_book_id', CURRENT_BOOK_ID); } catch(e) {}
  renderBookSelect();
}

function renderBookSelect() {
  const sel = document.getElementById('bookSelect');
  if (!sel) return;
  if (!BOOKS.length) {
    sel.innerHTML = '<option value="">暂无教材</option>';
    return;
  }
  sel.innerHTML = BOOKS.map(b => {
    const status = b.has_sections ? '' : '（未解析）';
    return `<option value="${esc(b.book_id)}" ${b.book_id === CURRENT_BOOK_ID ? 'selected' : ''}>${esc(b.title || b.book_id)}${status}</option>`;
  }).join('');
}

async function changeBook(bookId, force = false) {
  if (!bookId || (!force && bookId === CURRENT_BOOK_ID)) return;
  CURRENT_BOOK_ID = bookId;
  try { localStorage.setItem('current_book_id', CURRENT_BOOK_ID); } catch(e) {}
  clearBookScopedState();
  renderBookSelect();
  await fetchQuestions();
  await loadAnalysisReports();
  await loadExamHistory();
  if (document.getElementById('tab-quiz').classList.contains('active')) initQuiz();
  if (document.getElementById('tab-profile').classList.contains('active')) loadProfile();
}

function openBookDialog() {
  bookIdEdited = false;
  const dialog = document.getElementById('bookDialog');
  const title = document.getElementById('newBookTitle');
  const bookId = document.getElementById('newBookId');
  const pdf = document.getElementById('newBookPdf');
  const status = document.getElementById('bookUploadStatus');
  const btn = document.getElementById('bookUploadBtn');
  if (title) title.value = '';
  if (bookId) bookId.value = '';
  if (pdf) pdf.value = '';
  if (status) status.textContent = '';
  if (btn) btn.disabled = false;
  if (dialog) dialog.style.display = 'grid';
  setTimeout(() => { if (title) title.focus(); }, 30);
}

function closeBookDialog() {
  const dialog = document.getElementById('bookDialog');
  if (dialog) dialog.style.display = 'none';
}

function syncBookIdFromTitle() {
  if (bookIdEdited) return;
  const title = document.getElementById('newBookTitle');
  const bookId = document.getElementById('newBookId');
  if (title && bookId) bookId.value = makeBookId(title.value);
}

function markBookIdEdited() {
  bookIdEdited = true;
}

async function uploadBook() {
  const titleEl = document.getElementById('newBookTitle');
  const idEl = document.getElementById('newBookId');
  const fileEl = document.getElementById('newBookPdf');
  const status = document.getElementById('bookUploadStatus');
  const btn = document.getElementById('bookUploadBtn');
  const file = fileEl && fileEl.files && fileEl.files[0];
  const title = (titleEl && titleEl.value.trim()) || (file ? file.name.replace(/\.pdf$/i, '') : '');
  const bookId = makeBookId((idEl && idEl.value.trim()) || title);

  if (!title) { status.textContent = '请填写教材名称'; return; }
  if (!file) { status.textContent = '请选择 PDF 文件'; return; }
  if (!file.name.toLowerCase().endsWith('.pdf')) { status.textContent = '只支持 PDF 文件'; return; }

  const fd = new FormData();
  fd.append('title', title);
  fd.append('book_id', bookId);
  fd.append('pdf', file);

  btn.disabled = true;
  status.textContent = '上传中...';
  try {
    const res = await fetch('/api/books/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '上传失败');
    status.textContent = '已上传，等待解析...';
    const job = await pollBookJob(data.job_id);
    status.textContent = `解析完成：${job.title || title}`;
    await loadBooks(job.book_id || data.book_id);
    await changeBook(job.book_id || data.book_id, true);
    setTimeout(closeBookDialog, 700);
  } catch (e) {
    status.textContent = '失败：' + e.message;
    btn.disabled = false;
  }
}

async function pollBookJob(jobId) {
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 1800));
    const res = await fetch('/api/books/jobs/' + encodeURIComponent(jobId));
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '解析任务读取失败');
    const status = document.getElementById('bookUploadStatus');
    if (status) status.textContent = data.stage || data.status || '解析中...';
    if (data.status === 'done') return data;
    if (data.status === 'failed') throw new Error(data.error || '解析失败');
  }
}

// ═══════════════════════════════════
// Tab switching
// ═══════════════════════════════════
function switchTab(name, updateHash = true) {
  if (!['generate', 'quiz', 'profile', 'evals'].includes(name)) name = 'generate';
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
  if (updateHash && location.hash !== '#' + name) {
    history.replaceState(null, '', '#' + name);
  }
  if (name === 'quiz') initQuiz();
  if (name === 'profile') loadProfile();
  if (name === 'evals') loadEvalCenter();
}

// ═══════════════════════════════════
// Tab 1: Generate
// ═══════════════════════════════════

function selectMode(mode) {
  genMode = mode;
  document.querySelectorAll('.mode-card').forEach(c => c.classList.toggle('sel', c.dataset.mode === mode));

  const examOnly = (mode === 'exam');
  const diag = (mode === 'diagnostic');
  const prac = (mode === 'practice');

  // exam: show focus + analysis; diagnostic: hide both; practice: hide both
  document.getElementById('focusRow').style.display = examOnly ? '' : 'none';
  document.getElementById('analysisRow').style.display = examOnly ? '' : 'none';

  // diagnostic: force choice only
  if (diag) {
    document.querySelectorAll('#typeTags .type-tag').forEach(t => {
      t.classList.toggle('sel', t.dataset.type === 'choice');
    });
    document.getElementById('genCount').value = 0;
    document.getElementById('countHint').textContent = '0=自动（章数×2，≤30）';
  } else if (examOnly) {
    document.getElementById('countHint').textContent = '0=自动（6-12 自适应）';
  } else {
    document.getElementById('countHint').textContent = '0=自动（知识点×3，≤20）';
  }
}

function toggleType(el) {
  // diagnostic 不允许取消选择
  if (genMode === 'diagnostic' && el.dataset.type === 'choice') return;
  el.classList.toggle('sel');
}

function toggleDiff(el) {
  el.classList.toggle('sel');
}

async function loadAnalysisReports() {
  try {
    const res = await fetch('/api/analysis-reports' + bookParam());
    if (!res.ok) return;
    const reports = await res.json();
    const sel = document.getElementById('genAnalysis');
    // 清除旧选项（保留第一个"不参照"）
    while (sel.options.length > 1) sel.remove(1);
    reports.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.path;
      opt.textContent = `${r.filename}（${r.exam_count}份卷，${r.total_questions}题）`;
      sel.appendChild(opt);
    });
  } catch(e) {}
}

function uploadExamFile(input) {
  const file = input.files[0];
  if (!file) return;

  const status = document.getElementById('uploadStatus');
  status.textContent = '⏳ 分析中...';
  status.style.color = '#8B8680';

  const reader = new FileReader();
  reader.onload = function() {
    // 去掉 data:...;base64, 前缀
    const b64 = reader.result.split(',')[1];
    fetch('/api/analyze-exam', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(withBookPayload({ filename: file.name, data_base64: b64 })),
    }).then(r => r.json()).then(data => {
      if (data.ok) {
        status.textContent = `✅ ${data.filename}（${data.questions}题）`;
        status.style.color = '#4A7C59';
        loadAnalysisReports();  // 刷新下拉列表
        // 自动选中刚生成的报告
        setTimeout(() => {
          const sel = document.getElementById('genAnalysis');
          for (let i = 0; i < sel.options.length; i++) {
            if (sel.options[i].textContent.includes(data.filename)) {
              sel.selectedIndex = i;
              break;
            }
          }
        }, 300);
      } else {
        status.textContent = '❌ ' + (data.error || '分析失败');
        status.style.color = '#B55A4A';
      }
    }).catch(e => {
      status.textContent = '❌ 网络错误';
      status.style.color = '#B55A4A';
    });
  };
  reader.readAsDataURL(file);
  input.value = '';
}

async function doGenerate() {
  const btn = document.getElementById('genBtn');
  const status = document.getElementById('genStatus');
  btn.disabled = true;
  safeSetHTML(status, '<div class="loading-state" style="padding:20px;">出题任务已提交，请耐心等待...</div>');

  const selTypes = [];
  document.querySelectorAll('#typeTags .type-tag.sel').forEach(t => selTypes.push(t.dataset.type));
  const selDiffs = [];
  document.querySelectorAll('#diffTags .type-tag.sel').forEach(t => selDiffs.push(t.dataset.diff));
  const analysisReport = document.getElementById('genAnalysis').value;

  try {
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(withBookPayload({
        mode: genMode,
        count: parseInt(document.getElementById('genCount').value) || 0,
        types: selTypes.join(','),
        difficulty: selDiffs.join(','),
        focus: document.getElementById('genFocus').value.trim(),
        student_id: STUDENT_ID,
        analysis_report: analysisReport,
      })),
    });
    let data = await res.json();
    if (!data.ok) throw new Error(data.error || '未知错误');
    if (data.async && data.job_id) {
      data = await pollGenerateJob(data.job_id, status);
    }
    await finishGenerate(data, status);
  } catch (e) {
    btn.disabled = false;
    safeSetHTML(status, `<div class="empty-state" style="padding:20px;color:#B55A4A;">❌ 出题失败：${esc(e.message)}</div>`);
  }
}

async function pollGenerateJob(jobId, statusEl) {
  const startedAt = Date.now();
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 1600));
    const res = await fetch('/api/generate/jobs/' + encodeURIComponent(jobId));
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '出题任务读取失败');
    if (data.status === 'done') return data;
    if (data.status === 'failed') throw new Error(data.error || '出题失败');
    const stage = data.stage || data.status || '出题中';
    safeSetHTML(statusEl, `<div class="loading-state" style="padding:20px;">${esc(stage)}...</div>`);
    if (Date.now() - startedAt > 15 * 60 * 1000) {
      throw new Error('出题超时，请稍后重试');
    }
  }
}

async function finishGenerate(data, statusEl) {
  if (data.status && data.status !== 'done' && data.count == null) {
    throw new Error(data.error || '出题未完成');
  }
  CURRENT_SESSION_ID = data.session_id || null;
  if (CURRENT_SESSION_ID) { try { sessionStorage.setItem('current_session_id', CURRENT_SESSION_ID); } catch(e) {} }
  questions = [];
  answers = [];
  await fetchQuestions();
  loadExamHistory();
  const btn = document.getElementById('genBtn');
  if (btn) btn.disabled = false;
  safeSetHTML(statusEl, `<div class="gen-result">
    <div class="big">✅ ${data.count || 0} 题</div>
    <div style="color:#8B8680;margin:8px 0;">模式：${data.mode || genMode} · 已加载到答题区</div>
    <button class="btn btn-submit" style="margin-top:12px;width:auto;padding:12px 32px;" onclick="switchTab('quiz')">去答题 →</button>
  </div>`);
}

// ═══════════════════════════════════
// Tab 2: Quiz
// ═══════════════════════════════════

async function fetchQuestions() {
  try {
    const res = await fetch('/api/questions' + bookParam());
    if (res.ok) {
      const data = await res.json();
      questions = Array.isArray(data) ? data : [];
      return;
    }
  } catch(e) {}
  questions = [];
}

function initQuiz() {
  if (questions.length === 0) {
    safeSetHTML(document.getElementById('quizRoot'), '<div class="card loading-state">⏳ 加载题目中...</div>');
    fetchQuestions().then(() => {
      if (questions.length > 0) {
        answers = new Array(questions.length);
        qIdx = 0; qStartTs = Date.now(); confidence = 3;
        renderQuiz();
      } else {
        safeSetHTML(document.getElementById('quizRoot'), '<div class="card loading-state">📭 暂无题目，请先去"出题"Tab 生成试卷</div>');
      }
    });
    return;
  }
  if (document.getElementById('quizRoot').children.length === 0 ||
      document.getElementById('quizRoot').querySelector('.loading-state') ||
      document.getElementById('quizRoot').querySelector('.empty-state')) {
    answers = new Array(questions.length);
    qIdx = 0; qStartTs = Date.now(); confidence = 3;
    renderQuiz();
  }
}

function renderQuiz() {
  if (!questions.length) {
    safeSetHTML(document.getElementById('quizRoot'), '<div class="card loading-state">📭 暂无题目，请先去"出题"Tab 生成试卷</div>');
    return;
  }
  const q = questions[qIdx], total = questions.length;
  const ans = answers[qIdx] || {};
  const isChoice = q.question_type === 'choice';
  const elapsed = Math.floor((Date.now() - qStartTs) / 1000);
  const hasAns = ans.student_answer && ans.student_answer.trim();

  let segs = '';
  for (let i = 0; i < total; i++) segs += `<div class="seg${i<=qIdx?' done':''}"></div>`;

  let inputHtml = '';
  if (isChoice) {
    let o = '<div class="opts">';
    (q.options||[]).forEach((opt,i) => {
      let cls = labelOf(i) === ans.student_answer ? ' sel' : '';
      const optText = esc(String(opt || '').replace(/^[A-D][.、\s]+/,''));
      o += `<div class="opt${cls}" data-oi="${i}"><div class="dot">${labelOf(i)}</div><div class="txt">${optText}</div></div>`;
    });
    inputHtml = o + '</div>';
  } else {
    const ph = q.question_type==='fill_blank'?'请输入答案...':'请输入你的回答...';
    inputHtml = `<textarea class="tinp" id="textAns" rows="${q.question_type==='short_answer'?4:2}" placeholder="${ph}">${esc(ans.student_answer||'')}</textarea>`;
  }

  let starsHtml = '';
  for (let i=0;i<5;i++) starsHtml += `<span class="star${i<confidence?' on':''}" data-c="${i+1}">★</span>`;

  const isLast = qIdx === total - 1;
  const btnHtml = isLast
    ? `<button class="btn btn-finish" id="submitBtn" onclick="confirmSubmit()" ${!hasAns?'disabled':''}>交卷</button>`
    : `<button class="btn btn-submit" id="submitBtn" onclick="nextQ()" ${!hasAns?'disabled':''}>下一题 →</button>`;

  const quizRoot = document.getElementById('quizRoot');
  safeSetHTML(quizRoot, `
    <div class="card quiz-card">
      <div class="qinfo">
        <div class="qtopic">${esc(q.source||'')}  ${esc(q.topic||'')}</div>
        <div class="qmeta">
          <div class="qtimer">${String(Math.floor(elapsed/60)).padStart(2,'0')}:${String(elapsed%60).padStart(2,'0')}</div>
          <div class="badge ${q.question_type}">${TYPE_LABELS[q.question_type]||q.question_type}</div>
          <div class="badge ${q.difficulty}">${DIFF_LABELS[q.difficulty]||q.difficulty}</div>
        </div>
      </div>
      <div class="qprog">${segs}<div class="qnum">${String(qIdx+1).padStart(2,'0')}/${String(total).padStart(2,'0')}</div></div>
      <div class="qstem">${esc(stripMediaMarkers(q.stem))}</div>
      ${renderMediaList(q.media)}
      ${inputHtml}
      <div class="conf"><span class="clabel">把握度</span><div class="stars">${starsHtml}</div></div>
      <div class="btns">${btnHtml}</div>
    </div>`);
  renderMermaidBlocks(quizRoot);
}

// Delegate quiz events
document.addEventListener('click', (e) => {
  const zoom = e.target.closest('.media-zoom');
  if (zoom) {
    openImageViewer(zoom.dataset.src || '', zoom.dataset.title || '');
    return;
  }
  if (e.target.closest('.media-viewer-close') || e.target.classList.contains('media-viewer')) {
    closeImageViewer();
    return;
  }
  if (!document.getElementById('tab-quiz').classList.contains('active')) return;
  const opt = e.target.closest('.opt');
  if (opt && !document.querySelector('.opts.submitted')) {
    const oi = parseInt(opt.dataset.oi);
    if (!answers[qIdx]) answers[qIdx] = {};
    answers[qIdx].student_answer = labelOf(oi);
    document.querySelectorAll('.opt').forEach(el => el.classList.remove('sel'));
    opt.classList.add('sel');
    const btn = document.getElementById('submitBtn');
    if (btn) btn.disabled = false;
    return;
  }
  const star = e.target.closest('.star');
  if (star) {
    confidence = parseInt(star.dataset.c);
    document.querySelectorAll('.star').forEach((s, i) => s.classList.toggle('on', i < confidence));
    return;
  }
});

document.addEventListener('input', (e) => {
  if (e.target.id === 'textAns') {
    if (!answers[qIdx]) answers[qIdx] = {};
    answers[qIdx].student_answer = e.target.value;
    const btn = document.getElementById('submitBtn');
    if (btn) btn.disabled = !e.target.value.trim();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeImageViewer();
});

function nextQ() {
  const ans = answers[qIdx] || {};
  if (!ans.student_answer) return;
  ans.duration_sec = Math.floor((Date.now() - qStartTs) / 1000);
  ans.confidence = confidence;
  answers[qIdx] = ans;
  if (qIdx < questions.length - 1) {
    qIdx++;
    qStartTs = Date.now();
    confidence = 3;
    renderQuiz();
  }
}

async function confirmSubmit() {
  const ans = answers[qIdx] || {};
  if (!ans.student_answer) return;
  ans.duration_sec = Math.floor((Date.now() - qStartTs) / 1000);
  ans.confidence = confidence;
  answers[qIdx] = ans;

  const unanswered = [];
  questions.forEach((_, i) => { if (!answers[i] || !answers[i].student_answer || !answers[i].student_answer.trim()) unanswered.push(i + 1); });
  if (unanswered.length > 0 && !confirm(`第 ${unanswered.join(', ')} 题未作答，确定交卷？`)) return;
  if (unanswered.length === 0 && !confirm('确定交卷？')) return;

  safeSetHTML(document.getElementById('quizRoot'), '<div class="card loading-state">判题中，请稍候...</div>');

  const results = await submitExam();
  results.forEach((r, i) => {
    if (!answers[i]) answers[i] = {};
    answers[i].is_correct = r.is_correct;
    answers[i].reason = r.reason;
    answers[i].method = r.method;
    answers[i].attempt_id = r.attempt_id;
    answers[i].error_type = r.error_type || '';
  });
  showQuizResult(results);
}

async function submitExam() {
  const payload = {
    student_id: STUDENT_ID,
    session_id: CURRENT_SESSION_ID,
    book_id: CURRENT_BOOK_ID,
    answers: questions.map((q, i) => {
      const answer = {
        question_type: q.question_type, student_answer: (answers[i] && answers[i].student_answer) || '',
        correct_answer: q.correct_answer, stem: q.stem, explanation: q.explanation || '',
        source: q.source || '', topic: q.topic || '', difficulty: q.difficulty || '',
        duration_sec: (answers[i] && answers[i].duration_sec) || 0,
        confidence: (answers[i] && answers[i].confidence) || 3,
      };
      const media = mediaList(q.media);
      if (media.length) answer.media = media;
      return answer;
    }),
  };
  try {
    const res = await fetch('/api/submit-exam', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(60000),
    });
    if (res.ok) {
      const data = await res.json();
      if (data.ok) {
        CURRENT_SESSION_ID = null;
        try { sessionStorage.removeItem('current_session_id'); } catch(e) {}
        return data.results;
      }
    }
  } catch(e) {}
  return questions.map((q, i) => {
    const a = answers[i] || {};
    const given = String(a.student_answer || '').trim();
    const correct = String(q.correct_answer || '').trim();
    let ok = false;
    if (!given) ok = false;
    else if (q.question_type === 'choice') ok = given.toUpperCase() === correct.toUpperCase();
    else ok = given === correct;
    return { is_correct: ok, reason: '本地判定（降级）', method: 'fallback', correct_answer: q.correct_answer, explanation: q.explanation || '' };
  });
}

function showQuizResult(results) {
  const correct = results.filter(r => r && r.is_correct).length;
  const total = questions.length;
  const accuracy = total > 0 ? Math.round(correct / total * 100) : 0;
  const highConfWrong = results.filter((r, i) => r && !r.is_correct && (answers[i] && answers[i].confidence >= 4)).length;

  let reviewHtml = '';
  questions.forEach((q, i) => {
    const a = answers[i] || {}, r = results[i] || {};
    const ok = r.is_correct;
    const attemptId = r.attempt_id || a.attempt_id;
    const correctionHtml = attemptId ? `<div class="correction-actions">
      ${ok
        ? `<button class="mini-action" onclick="correctAttempt(${attemptId}, false, ${i})">标为错误</button>`
        : `<button class="mini-action" onclick="correctAttempt(${attemptId}, true, ${i})">标为正确</button>
           <button class="mini-action" onclick="changeErrorType(${attemptId}, ${i})">改错因</button>`}
    </div>` : '';
    reviewHtml += `<div class="ritem ${ok?'r-ok':'r-no'}">
      <div>${ok?'✓':'✗'}</div>
      <div>
        <div style="font-weight:500;margin-bottom:2px;">${i+1}. ${esc(stripMediaMarkers(q.stem))}</div>
        ${renderMediaList(q.media)}
        <div style="font-size:12px;color:#8B8680;">
          你的：${esc(a.student_answer||'未答')} | 正确：${esc(q.correct_answer)} | ${a.duration_sec||0}s | ${'★'.repeat(a.confidence||0)}
          ${r.method==='llm'?' | 🤖 LLM':r.method==='fallback'?' | ⚠️ 降级':''}
          ${r.reason?'<br>'+esc(r.reason):''}
          ${q.explanation?'<br><span style="color:#4A7C59;">💡 题解：'+esc(q.explanation)+'</span>':''}
        </div>
        ${correctionHtml}
      </div>
    </div>`;
  });

  const quizRoot = document.getElementById('quizRoot');
  safeSetHTML(quizRoot, `
    <div class="card qresult">
      <div class="big">${accuracy}<span>%</span></div>
      <div style="color:#8B8680;font-size:14px;">${accuracy>=80?'非常棒！':accuracy>=60?'不错，继续加油':'别灰心，多练几次'}</div>
      <div class="stats">
        <div class="stat"><div class="val">${correct}/${total}</div><div class="lbl">正确</div></div>
        <div class="stat"><div class="val">${accuracy}%</div><div class="lbl">正确率</div></div>
        <div class="stat"><div class="val">${highConfWrong}</div><div class="lbl">高信心错误</div></div>
      </div>
      <div class="review"><h3>答题回顾</h3>${reviewHtml}</div>
      <button class="btn btn-next" style="margin-top:16px;width:100%;" onclick="switchTab('profile')">查看画像 →</button>
      <button class="btn btn-submit" style="margin-top:8px;width:100%;" onclick="questions=[];switchTab('generate')">重新出题</button>
    </div>`);
  renderMermaidBlocks(quizRoot);
}

async function correctAttempt(attemptId, isCorrect, idx, errorType = '') {
  if (!attemptId) return;
  if (!isCorrect && !errorType) {
    errorType = await askErrorType();
    if (!errorType) return;
  }
  try {
    const res = await fetch('/api/attempt-correction', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(withBookPayload({ attempt_id: attemptId, is_correct: isCorrect, error_type: errorType })),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '修正失败');
    alert('已修正，刷新画像后生效。');
    if (answers[idx]) {
      answers[idx].is_correct = isCorrect;
      answers[idx].method = 'manual_override';
      answers[idx].error_type = isCorrect ? '' : errorType;
    }
  } catch (e) {
    alert('修正失败：' + e.message);
  }
}

async function changeErrorType(attemptId, idx) {
  const errorType = await askErrorType();
  if (!errorType) return;
  await correctAttempt(attemptId, false, idx, errorType);
}

async function askErrorType() {
  const lines = Object.entries(ERROR_TYPE_LABELS)
    .map(([key, label], i) => `${i + 1}. ${label} (${key})`)
    .join('\n');
  const input = prompt(`选择错因编号：\n${lines}`);
  if (!input) return '';
  const keys = Object.keys(ERROR_TYPE_LABELS);
  const n = parseInt(input, 10);
  if (n >= 1 && n <= keys.length) return keys[n - 1];
  if (ERROR_TYPE_LABELS[input]) return input;
  alert('未知错因');
  return '';
}

// ═══════════════════════════════════
// Tab 3: Profile
// ═══════════════════════════════════
async function loadProfile() {
  const root = document.getElementById('profileRoot');
  safeSetHTML(root, '<div class="loading-state">加载中...</div>');
  try {
    const res = await fetch('/api/profile' + bookParam());
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const d = await res.json();
    if (!d.ok) throw new Error(d.error || '未知错误');
    if (d.total_attempts === 0) {
      safeSetHTML(root, `<div class="empty-state">📭 暂无作答记录<br><span style="font-size:13px;color:#B5B0A8;">去 <a href="#" onclick="switchTab('quiz');return false;" style="color:#B8894F;">答题</a> 积累数据</span></div>`);
      return;
    }
    renderProfile(d);
  } catch(e) {
    safeSetHTML(root, `<div class="empty-state" style="color:#B55A4A;">❌ 加载失败：${e.message}</div>`);
  }
}

function renderProfile(d) {
  const mastery = d.mastery_summary || {};
  const topics = d.topics || [];
  const rec = d.recommendation || {};
  const errDist = d.error_distribution || {};
  const risks = d.risk_signals || [];

  let pills = '';
  for (const [k, v] of Object.entries(mastery)) {
    if (v > 0) pills += `<span class="pill ${k}">${ML_LABELS[k]||k} ${v}</span>`;
  }

  let html = `
  <div class="overview">
    <div class="stat-card"><div class="pval">${fmtPct(d.overall_accuracy)}</div><div class="plbl">整体正确率</div></div>
    <div class="stat-card"><div class="pval">${d.total_attempts}</div><div class="plbl">总作答次数</div></div>
    <div class="stat-card"><div class="pval">${topics.length}</div><div class="plbl">覆盖知识点</div></div>
    <div class="stat-card">
      <div class="pval" style="font-size:22px;">${mastery.mastered||0}<span style="font-size:14px;color:#8B8680;">/${topics.length}</span></div>
      <div class="plbl">已掌握</div>
      <div class="mastery-pills">${pills}</div>
    </div>
  </div>`;

  // Topics
  html += `<div class="card"><div class="sec-title">知识点掌握概率 · P(L) + 数据置信度</div>`;
  if (topics.length === 0) {
    html += '<div class="empty-state" style="padding:24px;">暂无知识点数据</div>';
  } else {
    for (const t of topics) {
      const bkt = t.bkt, bandit = t.bandit;
      const pL = bkt ? bkt.p_mastery : 0;
      const barColor = pLColor(pL);
      const confLevel = t.confidence_level || 'low';
      const confLabel = t.confidence_label || '数据不足';
      const confReason = t.confidence_reason || '';
      const betaKey = domKey(`${t.section_id}_${t.topic || ''}`);
      html += `<div class="topic-row">
        <div class="topic-name">
          <span class="tsid">${esc(t.section_id)}</span>${esc(t.topic) || esc(t.section_id)}
          ${t.dominant_error_type?`<span class="terr">${esc(t.dominant_error_type)}</span>`:''}
          <span class="ml-badge ${t.mastery_level}">${ML_LABELS[t.mastery_level]||t.mastery_level}</span>
          <div class="topic-meta">样本 ${t.evidence_count ?? (bkt?bkt.total_attempts:0)} 次
            <span class="conf-badge ${confLevel}" title="${esc(confReason)}">${esc(confLabel)}</span>
          </div>
        </div>
        <div class="pl-bar-wrap">
          <div class="pl-bar-bg"><div class="pl-bar-fill" style="width:${Math.round(pL*100)}%;background:${barColor};"></div></div>
          <div class="pl-bar-label">P(L)=${fmtPct(pL)} (${bkt?bkt.correct_count:0}/${bkt?bkt.total_attempts:0})</div>
        </div>`;
      if (bandit) {
        const betaId = 'bt_' + betaKey;
        html += `<div class="beta-mini"><canvas id="${betaId}" width="80" height="32"></canvas>
          <div class="beta-params">α=${bandit.alpha.toFixed(1)}<br>β=${bandit.beta.toFixed(1)}</div></div>`;
      } else {
        html += '<div></div>';
      }
      html += '</div>';
    }
  }
  html += '</div>';

  // Recommendation
  html += `<div class="card"><div class="sec-title">推荐练习计划</div>`;
  if (rec.items && rec.items.length > 0) {
    html += `<table class="rec-table"><thead><tr><th>#</th><th>章节</th><th>P(L)</th><th>难度</th><th>题型</th><th>题数</th><th>推荐原因</th></tr></thead><tbody>`;
    rec.items.forEach((item, i) => {
      html += `<tr><td style="color:#B8894F;font-weight:600;">${i+1}</td>
        <td>${esc(item.section_id)} ${esc(item.topic||'')}</td>
        <td>${fmtPct(item.p_mastery)}</td>
        <td>${DIFF_LABELS[item.difficulty]||item.difficulty}</td>
        <td>${(item.question_types||[]).join(', ')}</td>
        <td>${item.recommended_count}</td>
        <td class="reason-cell">${esc(item.reason_text || '-')}</td></tr>`;
    });
    html += `</tbody></table>`;
    html += `<div class="rec-reason">📋 ${esc(rec.reason)}（共 ${rec.target_count} 题）</div>`;
  } else {
    html += '<div class="empty-state" style="padding:24px;">暂无推荐数据</div>';
  }
  html += '</div>';

  // Error + Risk
  html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">';
  html += '<div class="card"><div class="sec-title">错因分布</div>';
  const errEntries = Object.entries(errDist);
  if (errEntries.length > 0) {
    const maxCnt = Math.max(...errEntries.map(e => e[1]));
    for (const [label, cnt] of errEntries) {
      html += `<div class="err-bar"><span class="err-label">${esc(label)}</span><div class="err-track"><div class="err-fill" style="width:${maxCnt>0?Math.round(cnt/maxCnt*100):0}%;"></div></div><span class="err-cnt">${cnt} 次</span></div>`;
    }
  } else { html += '<div class="empty-state" style="padding:24px;">暂无错因标签</div>'; }
  html += '</div>';
  html += '<div class="card"><div class="sec-title">风险信号</div>';
  if (risks.length > 0) {
    for (const r of risks) html += `<div class="risk-item">⚠️ ${esc(r)}</div>`;
  } else { html += '<div class="empty-state" style="padding:24px;">✅ 无异常信号</div>'; }
  html += '</div></div>';

  // ── Recent Sessions ──
  const sessions = d.recent_sessions || [];
  if (sessions.length > 0) {
    html += `<div class="card" style="margin-top:16px;"><div class="sec-title">最近练习记录</div>`;
    html += `<table class="rec-table" style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr style="color:#8B8680;text-align:left;border-bottom:1px solid rgba(0,0,0,0.06);">
      <th style="padding:8px 6px;">#</th><th>模式</th><th>时间</th><th>题数</th><th>正确率</th><th>效果</th></tr></thead><tbody>`;
    sessions.forEach((s, i) => {
      const acc = s.accuracy != null ? Math.round(s.accuracy * 100) + '%' : '-';
      const effect = s.effect_summary || '-';
      const modeLabel = {practice:'训练',diagnostic:'摸底',exam:'考试',historical:'历史'}[s.mode] || s.mode;
      html += `<tr style="border-bottom:1px solid rgba(0,0,0,0.03);">
        <td style="padding:6px;color:#B8894F;font-weight:500;">${i+1}</td>
        <td style="padding:6px;">${modeLabel}</td>
        <td style="padding:6px;font-size:11px;color:#8B8680;">${esc(s.ended_at || s.started_at || '')}</td>
        <td style="padding:6px;">${s.attempt_count}</td>
        <td style="padding:6px;font-weight:500;">${acc}</td>
        <td style="padding:6px;font-size:12px;color:#5A5650;">${esc(effect)}</td></tr>`;
    });
    html += `</tbody></table></div>`;
  }

  // ── Trend Summary ──
  const trend = d.trend_summary || {};
  if (trend.overall_trend && trend.overall_trend !== 'insufficient_data') {
    html += `<div class="card" style="margin-top:16px;"><div class="sec-title">最近趋势</div>`;
    const trendLabels = { improving: '📈 提升中', declining: '📉 下降中', stable: '➡️ 稳定' };
    html += `<div style="margin-bottom:10px;font-size:14px;color:#2E2C29;">整体趋势：${trendLabels[trend.overall_trend] || trend.overall_trend}（近 ${trend.session_count || '?'} 次练习）</div>`;
    const sections = [
      { label: '提升知识点', items: trend.improving_topics || [], color: '#4A7C59' },
      { label: '下降知识点', items: trend.declining_topics || [], color: '#B55A4A' },
      { label: '卡住知识点', items: trend.stalled_topics || [], color: '#D4956A' },
    ];
    sections.forEach(s => {
      if (!s.items.length) return;
      html += `<div style="margin-bottom:6px;font-size:12px;font-weight:500;color:#5A5650;">${s.label}</div>`;
      s.items.forEach(t => {
        const deltaStr = (t.avg_delta > 0 ? '+' : '') + (t.avg_delta * 100).toFixed(1) + '%';
        html += `<div style="display:flex;gap:10px;padding:3px 0;font-size:13px;color:#3A3632;">
          <span style="font-family:'SF Mono',monospace;font-size:11px;color:#8B8680;">${esc(t.section_id)}</span>
          <span style="flex:1;">${esc(t.trend || '')}</span>
          <span style="font-family:'SF Mono',monospace;font-size:12px;color:${s.color};">${deltaStr}</span></div>`;
      });
    });
    html += `</div>`;
  }

  // ── Memory Facts ──
  const facts = d.memory_facts || [];
  if (facts.length > 0) {
    html += `<div class="card" style="margin-top:16px;"><div class="sec-title">长期记忆</div>`;
    const typeIcons = { weak_topic: '⚠️', trend: '📊', error_pattern: '🔄', risk: '🚨', strategy_effect: '💡' };
    facts.forEach(f => {
      const confidence = Math.round(f.confidence * 100);
      const icon = typeIcons[f.memory_type] || '📌';
      let desc = '';
      if (f.memory_type === 'weak_topic') {
        desc = `${esc(f.memory_key)} 长期薄弱`;
      } else if (f.memory_type === 'trend') {
        desc = `${esc(f.memory_key)}`;
      } else if (f.memory_type === 'error_pattern') {
        desc = `${esc(f.memory_key)} 频发`;
      } else {
        desc = JSON.stringify(f.value_json || f.memory_key);
      }
      html += `<div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid rgba(0,0,0,0.03);font-size:13px;color:#3A3632;">
        <span style="flex-shrink:0;">${icon}</span>
        <span style="flex:1;">${desc}</span>
        <span class="badge" style="font-size:10px;background:rgba(184,137,79,0.1);border-color:transparent;">${confidence}%</span></div>`;
    });
    html += `</div>`;
  }

  safeSetHTML(document.getElementById('profileRoot'), html);

  // Draw Beta canvases
  for (const t of topics) {
    const betaKey = domKey(`${t.section_id}_${t.topic || ''}`);
    if (t.bandit) drawBeta('bt_' + betaKey, t.bandit.alpha, t.bandit.beta);
  }
}


// ═══════════════════════════════════
// Tab 4: Eval center
// ═══════════════════════════════════
let evalRuns = [];
let evalCurrentRunId = '';

async function loadEvalCenter(runId = '') {
  const root = document.getElementById('evalRoot');
  if (!root) return;
  safeSetHTML(root, '<div class="card loading-state">加载评测报告中...</div>');
  try {
    const listRes = await fetch('/api/evals');
    const listData = await listRes.json();
    if (!listData.ok) throw new Error(listData.error || '评测列表读取失败');
    evalRuns = listData.runs || [];
    if (!evalRuns.length) {
      safeSetHTML(root, '<div class="card empty-state">暂无评测报告</div>');
      return;
    }
    const selectedRunId = runId || evalCurrentRunId || evalRuns[0].run_id;
    await loadEvalRun(selectedRunId, false);
  } catch (e) {
    safeSetHTML(root, `<div class="card empty-state" style="color:#B55A4A;">加载失败：${esc(e.message)}</div>`);
  }
}

async function loadEvalRun(runId, keepList = true) {
  const root = document.getElementById('evalRoot');
  if (!root || !runId) return;
  evalCurrentRunId = runId;
  if (!keepList || !evalRuns.length) {
    const listRes = await fetch('/api/evals');
    const listData = await listRes.json();
    evalRuns = listData.runs || [];
  }
  safeSetHTML(root, '<div class="card loading-state">加载评测详情中...</div>');
  try {
    const res = await fetch('/api/evals/' + encodeURIComponent(runId));
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '评测报告读取失败');
    renderEvalCenter(data.report, data.run || {});
  } catch (e) {
    safeSetHTML(root, `<div class="card empty-state" style="color:#B55A4A;">加载失败：${esc(e.message)}</div>`);
  }
}

function renderEvalCenter(report, run) {
  const root = document.getElementById('evalRoot');
  const metrics = report.metrics || [];
  const meta = report.metadata || {};
  const sections = meta.sections || {};
  const diffs = meta.metric_diffs || run.diffs || [];
  const failures = report.failures || [];
  const scoreMetric = metricByName(metrics, 'overall_score');
  const generationMetric = metricByName(metrics, 'generation_score');
  const judgeMetric = metricByName(metrics, 'judge_score');
  const recommendationMetric = metricByName(metrics, 'recommendation_score');
  const overall = scoreMetric ? scoreMetric.value : (run.score || 0);
  const regressions = diffs.filter(d => d.status === 'regressed');
  const improvements = diffs.filter(d => d.status === 'improved');

  const selectedOptions = evalRuns.map(r => {
    const label = `${formatRunTime(r.created_at)} · ${formatMetricValue(r.score || 0)}`;
    return `<option value="${esc(r.run_id)}" ${r.run_id === report.run_id ? 'selected' : ''}>${esc(label)}</option>`;
  }).join('');

  let html = `
    <div class="card">
      <div class="eval-topline">
        <div>
          <div class="eval-title">评测中心</div>
          <div class="eval-subtitle">读取本地 evals/reports 报告，展示完整评测、回归对比和失败样本。</div>
        </div>
        <div class="eval-actions">
          <select class="eval-select" onchange="loadEvalRun(this.value)">${selectedOptions}</select>
          <button class="mini-action" onclick="loadEvalCenter(evalCurrentRunId)">刷新</button>
        </div>
      </div>
      <div class="eval-hero">
        <div class="score-ring" style="--score-deg:${Math.round((overall || 0) * 360)}deg;">
          <div class="score-inner">
            <div class="score-value">${formatMetricValue(overall)}</div>
            <div class="score-label">overall</div>
          </div>
        </div>
        <div>
          <div class="eval-score-grid">
            ${renderEvalScoreItem('出题', generationMetric)}
            ${renderEvalScoreItem('判题', judgeMetric)}
            ${renderEvalScoreItem('推荐', recommendationMetric)}
          </div>
          <div class="eval-meta-line">
            <span class="eval-chip">Run ${esc(report.run_id || '')}</span>
            <span class="eval-chip">${esc(formatRunTime(report.created_at || run.created_at))}</span>
            <span class="eval-chip">${failures.length} 个失败项</span>
            <span class="eval-chip">${improvements.length} 个提升</span>
            <span class="eval-chip">${regressions.length} 个回退</span>
          </div>
          <div class="eval-subtitle" style="margin-top:12px;">${esc(report.summary || run.summary || '')}</div>
        </div>
      </div>
    </div>

    <div class="eval-section-grid">
      ${renderEvalSection('generation', '出题质量', sections.generation)}
      ${renderEvalSection('judge', '判题质量', sections.judge)}
      ${renderEvalSection('recommendation', '推荐质量', sections.recommendation)}
    </div>

    <div class="card">
      <div class="sec-title">核心指标</div>
      ${renderMetricTable(metrics)}
    </div>

    <div class="card">
      <div class="sec-title">指标回归</div>
      ${renderDiffTable(diffs)}
    </div>

    <div class="card">
      <div class="sec-title">失败样本</div>
      ${renderFailureList(failures)}
    </div>

    <div class="card">
      <div class="sec-title">历史运行</div>
      ${renderEvalHistory()}
    </div>
  `;

  safeSetHTML(root, html);
}

function metricByName(metrics, name) {
  return (metrics || []).find(m => m.name === name);
}

function renderEvalScoreItem(label, metric) {
  const value = metric ? metric.value : 0;
  const passed = !metric || metric.passed;
  return `<div class="eval-score-item">
    <div class="label">${esc(label)} ${passed ? '<span class="status-pill pass">PASS</span>' : '<span class="status-pill fail">FAIL</span>'}</div>
    <div class="value">${formatMetricValue(value)}</div>
  </div>`;
}

function renderEvalSection(key, title, section) {
  const metrics = (section && section.metrics) || [];
  const passed = metrics.filter(m => m.passed).length;
  const total = metrics.length;
  const score = total ? passed / total : 0;
  return `<div class="eval-section">
    <div class="name">${esc(title)}</div>
    <div class="status">${formatMetricValue(score)}</div>
    <div class="summary">${esc((section && section.summary) || '未运行')}</div>
    <div class="eval-meta-line" style="margin-top:10px;">
      <span class="eval-chip">${passed}/${total} 指标</span>
      <span class="eval-chip">${(section && section.failure_count) || 0} 失败</span>
    </div>
  </div>`;
}

function renderMetricTable(metrics) {
  if (!metrics || !metrics.length) return '<div class="empty-state" style="padding:24px;">暂无指标</div>';
  let rows = metrics.map(m => `<tr>
    <td><span class="metric-name">${esc(m.name)}</span></td>
    <td>${formatMetricValue(m.value)}</td>
    <td>${m.threshold == null ? '-' : formatMetricValue(m.threshold)}</td>
    <td><span class="status-pill ${m.passed ? 'pass' : 'fail'}">${m.passed ? 'PASS' : 'FAIL'}</span></td>
    <td>${esc(m.detail || '')}</td>
  </tr>`).join('');
  return `<div class="eval-table-wrap"><table class="eval-table">
    <thead><tr><th>指标</th><th>数值</th><th>阈值</th><th>结果</th><th>说明</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderDiffTable(diffs) {
  if (!diffs || !diffs.length) return '<div class="empty-state" style="padding:24px;">暂无上一轮对比</div>';
  const sorted = diffs.slice().sort((a, b) => statusWeight(a.status) - statusWeight(b.status));
  let rows = sorted.map(d => `<tr>
    <td><span class="metric-name">${esc(d.name)}</span></td>
    <td>${formatMaybeMetric(d.current)}</td>
    <td>${formatMaybeMetric(d.previous)}</td>
    <td>${formatDelta(d.delta)}</td>
    <td><span class="status-pill ${esc(d.status || 'stable')}">${esc(d.status || 'stable')}</span></td>
  </tr>`).join('');
  return `<div class="eval-table-wrap"><table class="eval-table">
    <thead><tr><th>指标</th><th>本次</th><th>上次</th><th>变化</th><th>状态</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderFailureList(failures) {
  if (!failures || !failures.length) return '<div class="empty-state" style="padding:24px;">无失败样本</div>';
  return `<div class="failure-list">${failures.map(f => {
    const evidence = JSON.stringify(f.evidence || {}, null, 2);
    return `<div class="failure-item">
      <div class="failure-head">
        <span>${esc(f.case_id || '')} · ${esc(f.item_id || '')}</span>
        <span>${esc(f.reason || '')}</span>
      </div>
      <div class="failure-evidence">${esc(evidence).slice(0, 800)}</div>
    </div>`;
  }).join('')}</div>`;
}

function renderEvalHistory() {
  if (!evalRuns.length) return '<div class="empty-state" style="padding:24px;">暂无历史记录</div>';
  return evalRuns.slice(0, 10).map(r => `<div class="history-run">
    <div class="history-main">
      <div class="history-id"><a href="#" onclick="loadEvalRun('${esc(r.run_id)}');return false;" style="color:#8A6538;text-decoration:none;">${esc(r.run_id)}</a></div>
      <div class="history-summary">${esc(r.summary || '')}</div>
    </div>
    <div class="history-score">${formatMetricValue(r.score || 0)}</div>
  </div>`).join('');
}

function formatMetricValue(value) {
  if (value === '' || value == null || isNaN(Number(value))) return '-';
  return Math.round(Number(value) * 100) + '%';
}

function formatMaybeMetric(value) {
  if (value === '' || value == null) return '-';
  return formatMetricValue(value);
}

function formatDelta(value) {
  if (value === '' || value == null || isNaN(Number(value))) return '-';
  const pct = Math.round(Number(value) * 100);
  return (pct > 0 ? '+' : '') + pct + '%';
}

function statusWeight(status) {
  return { regressed: 0, improved: 1, new: 2, stable: 3 }[status] ?? 4;
}

function formatRunTime(value) {
  if (!value) return '';
  return String(value).replace('T', ' ').slice(0, 16);
}


// ═══════════════════════════════════
// Exam history
// ═══════════════════════════════════
async function loadExamHistory() {
  var root = document.getElementById('examHistory');
  if (!root) return;
  try {
    var res = await fetch('/api/exams' + bookParam());
    var exams = await res.json();
    if (!Array.isArray(exams)) exams = [];
    if (!exams.length) { root.innerHTML = '<div style=\"color:#8B8680;font-size:13px;\">暂无历史试卷</div>'; return; }
    var html = '';
    exams.slice(0, 10).forEach(function(e) {
      var ts = e.timestamp;
      var label = ts ? ts.slice(0,4)+'-'+ts.slice(4,6)+'-'+ts.slice(6,8)+' '+ts.slice(9,11)+':'+ts.slice(11,13) : e.filename;
      html += `<div class="exam-item" style="padding:8px 0;border-bottom:1px solid rgba(0,0,0,0.05);">
        <span style="cursor:pointer;color:#B8894F;font-weight:500;" data-file="${e.filename}" onclick="toggleExam(this.dataset.file)">${label} - ${e.count} 题</span>
        <div id="exam_${e.filename}" style="display:none;margin-top:8px;"></div>
      </div>`;
    });
    root.innerHTML = html;
  } catch(e) { root.innerHTML = '<div style=\"color:#B55A4A;font-size:13px;\">加载失败</div>'; }
}

async function toggleExam(filename) {
  var el = document.getElementById('exam_' + filename);
  if (!el) return;
  if (el.style.display === 'none') {
    el.style.display = 'block';
    if (!el.dataset.loaded) {
      el.innerHTML = '<div class=\"loading-state\">加载中...</div>';
      try {
        var res = await fetch('/api/exams/' + encodeURIComponent(filename) + bookParam());
        var qs = await res.json();
        var html = '';
        qs.forEach(function(q, i) {
          var opts = q.options && q.options.length ? q.options.map(function(o) { return '<span style=\"display:inline-block;margin:2px 8px 2px 0;\">' + renderLatex(esc(o)) + '</span>'; }).join('') : '';
          html += '<div style=\"margin-bottom:12px;padding:10px;background:rgba(255,255,255,0.3);border-radius:10px;\">'
            + '<div style=\"font-weight:600;margin-bottom:4px;\">' + (i+1) + '. [' + esc(q.question_type||'') + '] ' + renderLatex(esc(stripMediaMarkers(q.stem||''))) + '</div>'
            + renderMediaList(q.media)
            + (opts ? '<div style=\"font-size:13px;color:#8B8680;margin-bottom:4px;\">' + opts + '</div>' : '')
            + '<div style=\"font-size:12px;color:#4A7C59;\">答案：' + renderLatex(esc(q.correct_answer||'')) + '</div>'
            + (q.explanation ? '<div style=\"font-size:12px;color:#8B8680;\">解析：' + renderLatex(esc(q.explanation)) + '</div>' : '')
            + '<div style=\"font-size:11px;color:#B5B0A8;\">难度：' + esc(q.difficulty||'') + ' · 章节：' + esc(q.source||'') + ' ' + esc(q.topic||'') + '</div>'
            + '</div>';
        });
        el.innerHTML = html;
        renderMermaidBlocks(el);
        el.dataset.loaded = '1';
      } catch(e) { el.innerHTML = '<div style=\"color:#B55A4A;\">加载失败</div>'; }
    }
  } else { el.style.display = 'none'; }
}

async function initApp() {
  try {
    await loadBooks();
    await loadAnalysisReports();
    await loadExamHistory();
    var _initTab = location.hash ? location.hash.slice(1) : "generate";
    if (['quiz', 'profile', 'evals'].includes(_initTab)) {
      switchTab(_initTab, false);
    } else {
      await fetchQuestions();
    }
  } catch (e) {
    const root = document.getElementById('tab-generate');
    if (root) {
      root.innerHTML = '<div class="card empty-state" style="color:#B55A4A;padding:24px;">加载失败：' + esc(e.message) + '</div>';
    }
  }
}

initApp();
