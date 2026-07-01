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
