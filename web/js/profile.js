// ═══════════════════════════════════
// Tab 3: Profile
// ═══════════════════════════════════
async function loadProfile() {
  const root = document.getElementById('profileRoot');
  safeSetHTML(root, '<div class="loading-state">加载中...</div>');
  try {
    const res = await fetch('/api/profile');
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
