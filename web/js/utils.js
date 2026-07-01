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

function labelOf(i) { return String.fromCharCode(65+i); }
function fmtPct(v) { return Math.round((v||0)*100)+'%'; }
function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function domKey(s) { return Array.from(String(s||'')).map(c => c.charCodeAt(0).toString(36)).join('_'); }
function pLColor(p) { if(p<0.3)return'#B55A4A'; if(p<0.5)return'#D4956A'; if(p<0.7)return'#C49A5E'; if(p<0.85)return'#8AAA6A'; return'#4A7C59'; }
