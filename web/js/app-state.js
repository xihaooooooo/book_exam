const STUDENT_ID = "default";
let CURRENT_SESSION_ID = (() => {
  try { const v = sessionStorage.getItem('current_session_id'); return v ? parseInt(v) : null; } catch(e) { return null; }
})();
const TYPE_LABELS = { choice:'选择题', fill_blank:'填空题', short_answer:'简答题', comprehensive:'综合题' };
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
const typeMap = { '选择题': 'choice', '填空题': 'fill_blank', '简答题': 'short_answer' };

let questions = [], qIdx = 0, qStartTs = Date.now(), confidence = 3, answers = [];
