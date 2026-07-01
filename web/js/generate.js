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
  } else if (exam) {
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

async function loadAnalysisReports() {
  try {
    const res = await fetch('/api/analysis-reports');
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
      body: JSON.stringify({ filename: file.name, data_base64: b64 }),
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

function doGenerate() {
  const btn = document.getElementById('genBtn');
  const status = document.getElementById('genStatus');
  btn.disabled = true;
  safeSetHTML(status, '<div class="loading-state" style="padding:20px;">⏳ 出题中，请耐心等待（约 30-60 秒）...</div>');

  const selTypes = [];
  document.querySelectorAll('#typeTags .type-tag.sel').forEach(t => selTypes.push(typeMap[t.dataset.type]));
  const analysisReport = document.getElementById('genAnalysis').value;

  fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      mode: genMode,
      count: parseInt(document.getElementById('genCount').value) || 0,
      types: selTypes.join(','),
      focus: document.getElementById('genFocus').value.trim(),
      student_id: STUDENT_ID,
      analysis_report: analysisReport,
    }),
  }).then(r => r.json()).then(data => {
    btn.disabled = false;
    if (data.ok) {
      CURRENT_SESSION_ID = data.session_id || null;
      if (CURRENT_SESSION_ID) { try { sessionStorage.setItem('current_session_id', CURRENT_SESSION_ID); } catch(e) {} }
      safeSetHTML(status, `<div class="gen-result">
        <div class="big">✅ ${data.count} 题</div>
        <div style="color:#8B8680;margin:8px 0;">模式：${data.mode} · 已加载到答题区</div>
        <button class="btn btn-submit" style="margin-top:12px;width:auto;padding:12px 32px;" onclick="switchTab('quiz')">去答题 →</button>
      </div>`);
    } else {
      safeSetHTML(status, `<div class="empty-state" style="padding:20px;color:#B55A4A;">❌ ${data.error || '未知错误'}</div>`);
    }
  }).catch(e => {
    btn.disabled = false;
    safeSetHTML(status, `<div class="empty-state" style="padding:20px;color:#B55A4A;">❌ 网络错误：${e.message}</div>`);
  });
}
