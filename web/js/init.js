// ═══════════════════════════════════
// Init — 页面启动引导（最后加载）
// ═══════════════════════════════════
(function () {
  var msg = '';
  try {
    if (typeof loadAnalysisReports !== 'function') throw new Error('loadAnalysisReports 未定义');
    loadAnalysisReports();
    var initialTab = location.hash ? location.hash.slice(1) : 'generate';
    if (['quiz', 'profile'].includes(initialTab)) {
      if (typeof switchTab !== 'function') throw new Error('switchTab 未定义');
      switchTab(initialTab, false);
    } else {
      if (typeof fetchQuestions !== 'function') throw new Error('fetchQuestions 未定义');
      fetchQuestions();
    }
  } catch (e) {
    console.error('Init failed:', e);
    msg = e.message || String(e);
  }
  // 只在真正出错时才覆盖页面
  if (msg) {
    document.getElementById('tab-generate').innerHTML =
      '<div class="card empty-state" style="color:#B55A4A;padding:24px;">加载失败：' + msg + '</div>';
  }
})();
