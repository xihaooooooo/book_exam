// ═══════════════════════════════════
// Tab 2: Quiz
// ═══════════════════════════════════

async function fetchQuestions() {
  try {
    const res = await fetch('/api/questions');
    if (res.ok) { questions = await res.json(); return; }
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
      o += `<div class="opt${cls}" data-oi="${i}"><div class="dot">${labelOf(i)}</div><div class="txt">${opt.replace(/^[A-D][.、\s]+/,'')}</div></div>`;
    });
    inputHtml = o + '</div>';
  } else {
    const ph = q.question_type==='fill_blank'?'请输入答案...':'请输入你的回答...';
    inputHtml = `<textarea class="tinp" id="textAns" rows="${q.question_type==='short_answer'?4:2}" placeholder="${ph}">${ans.student_answer||''}</textarea>`;
  }

  let starsHtml = '';
  for (let i=0;i<5;i++) starsHtml += `<span class="star${i<confidence?' on':''}" data-c="${i+1}">★</span>`;

  const isLast = qIdx === total - 1;
  const btnHtml = isLast
    ? `<button class="btn btn-finish" id="submitBtn" onclick="confirmSubmit()" ${!hasAns?'disabled':''}>交卷</button>`
    : `<button class="btn btn-submit" id="submitBtn" onclick="nextQ()" ${!hasAns?'disabled':''}>下一题 →</button>`;

  safeSetHTML(document.getElementById('quizRoot'), `
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
      <div class="qstem">${esc(q.stem)}</div>
      ${inputHtml}
      <div class="conf"><span class="clabel">把握度</span><div class="stars">${starsHtml}</div></div>
      <div class="btns">${btnHtml}</div>
    </div>`);
}

// Delegate quiz events
document.addEventListener('click', (e) => {
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
    answers: questions.map((q, i) => ({
      question_type: q.question_type, student_answer: (answers[i] && answers[i].student_answer) || '',
      correct_answer: q.correct_answer, stem: q.stem, explanation: q.explanation || '',
      source: q.source || '', topic: q.topic || '', difficulty: q.difficulty || '',
      duration_sec: (answers[i] && answers[i].duration_sec) || 0,
      confidence: (answers[i] && answers[i].confidence) || 3,
    })),
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
        <div style="font-weight:500;margin-bottom:2px;">${i+1}. ${esc(q.stem)}</div>
        <div style="font-size:12px;color:#8B8680;">
          你的：${esc(a.student_answer||'未答')} | 正确：${esc(q.correct_answer)} | ${a.duration_sec||0}s | ${'★'.repeat(a.confidence||0)}
          ${r.method==='llm'?' | 🤖 LLM':r.method==='fallback'?' | ⚠️ 降级':''}
          ${r.reason?'<br>'+esc(r.reason):''}
        </div>
        ${correctionHtml}
      </div>
    </div>`;
  });

  safeSetHTML(document.getElementById('quizRoot'), `
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
      body: JSON.stringify({ attempt_id: attemptId, is_correct: isCorrect, error_type: errorType }),
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

// Delegate quiz events
document.addEventListener('click', (e) => {
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
