// ═══════════════════════════════════
// Tab switching
// ═══════════════════════════════════
function switchTab(name, updateHash = true) {
  if (!['generate', 'quiz', 'profile'].includes(name)) name = 'generate';
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
  if (updateHash && location.hash !== '#' + name) {
    history.replaceState(null, '', '#' + name);
  }
  if (name === 'quiz') initQuiz();
  if (name === 'profile') loadProfile();
}
