"""スマホ用の操作画面（セッティングモード：スクリーンの前で四隅を合わせる）。
同じ Wi-Fi のスマホで http://PCのアドレス:8080/m を開く（project.json の allow_lan を true にしたとき）"""

PAGE = r"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#0a0b0d">
<title>VPT39 セッティング</title>
<style>
:root{--bg:#0e1013;--panel:#171a1f;--panel2:#1f232a;--line:#2b3038;--text:#e6e8eb;--dim:#8a919c;
--acc:#3dd68c;--warn:#ffb020;--red:#ff4d4f;--hov:#a9ecc9;--hovtx:#06291a}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);font:15px/1.4 system-ui,"Hiragino Sans","Yu Gothic UI",sans-serif;
  touch-action:manipulation;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
body{padding:calc(8px + env(safe-area-inset-top)) 12px calc(16px + env(safe-area-inset-bottom))}
header{display:flex;align-items:center;gap:8px;margin-bottom:8px}
h1{font-size:20px;margin:0;color:var(--acc);font-weight:800;letter-spacing:.05em}
h1 small{font-size:12px;color:var(--dim);font-weight:400;margin-left:6px;letter-spacing:0}
.grow{flex:1}
.dot{width:10px;height:10px;border-radius:50%;background:var(--red);flex:none}
.dot.ok{background:var(--acc)}
button{font:inherit;color:var(--text);background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 12px;cursor:pointer}
button:active{background:var(--hov);border-color:var(--hov);color:var(--hovtx)}
button.on{background:var(--acc);border-color:var(--acc);color:#04120a;font-weight:700}
button.held{background:var(--red);border-color:var(--red);color:#fff;font-weight:700}
button:disabled{opacity:.35}
select{font:inherit;color:var(--text);background:#0c0e11;border:1px solid var(--line);border-radius:10px;padding:10px;width:100%}
.row{display:flex;gap:8px;align-items:center;margin:8px 0}
.lbl{color:var(--dim);font-size:13px;min-width:4.5em}
#map{width:100%;display:block;background:#000;border:1px solid var(--line);border-radius:10px;touch-action:none}
.pad{display:grid;grid-template-columns:1fr 1.25fr;gap:10px;margin-top:10px}
.corners,.dpad{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.corners button,.dpad button{padding:0;height:52px;font-size:14px}
.dpad button{font-size:22px}
.dpad .step{font-size:14px;font-weight:700}
.corners .all{font-size:13px}
.cap{color:var(--dim);font-size:12px;margin:2px 0 4px}
input[type=range]{flex:1;accent-color:var(--acc);height:28px}
.val{width:3em;text-align:right;color:var(--dim);font-size:13px}
.tools{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}
.tools button{padding:12px 4px;font-size:14px}
#off{display:none;position:fixed;left:12px;right:12px;top:calc(8px + env(safe-area-inset-top));z-index:9;background:var(--red);color:#fff;
  border-radius:10px;padding:12px;font-weight:700;text-align:center}
body.lost #off{display:block}
body.lost main{opacity:.35;pointer-events:none}
#toast{position:fixed;left:50%;bottom:calc(16px + env(safe-area-inset-bottom));transform:translateX(-50%);background:#000d;color:#fff;
  padding:8px 14px;border-radius:20px;font-size:13px;display:none;max-width:90vw;text-align:center}
.hint{color:var(--dim);font-size:12px;margin-top:12px;line-height:1.5}
</style></head><body>
<div id="off">PCとつながっていません（Wi-Fi・VPT39 を確認）</div>
<main>
<header><span class="dot" id="dot"></span><h1>VPT39<small>セッティング</small></h1><span class="grow"></span>
  <button id="edit" title="スクリーンに黄色い枠と赤い印を出す">枠を表示</button></header>
<div class="row"><span class="lbl">レイヤー</span><select id="layer"></select></div>
<div class="row" id="outrow"><span class="lbl">出力</span><select id="out"></select></div>
<canvas id="map" width="800" height="450"></canvas>
<div class="pad">
  <div><div class="cap">動かす所</div>
    <div class="corners">
      <button data-c="0">左上</button><span></span><button data-c="1">右上</button>
      <span></span><button data-c="4" class="all">全体</button><span></span>
      <button data-c="3">左下</button><span></span><button data-c="2">右下</button>
    </div></div>
  <div><div class="cap">押す＝1回・長押し＝続けて</div>
    <div class="dpad">
      <span></span><button data-d="0,-1">▲</button><span></span>
      <button data-d="-1,0">◀</button><button class="step" id="step">1px</button><button data-d="1,0">▶</button>
      <span></span><button data-d="0,1">▼</button><span></span>
    </div></div>
</div>
<div class="tools">
  <button id="undo">↶ 1つ戻す</button><button id="test">テスト模様</button><button id="reset">四隅リセット</button>
</div>
<div class="row"><span class="lbl">不透明度</span><input type="range" id="op" min="0" max="1" step="0.01"><span class="val" id="opv"></span></div>
<div class="row"><span class="lbl">ぼかし</span><input type="range" id="fe" min="0" max="0.25" step="0.005"><span class="val" id="fev"></span></div>
<div class="hint">「枠を表示」にすると、スクリーンの映像に黄色い枠と赤い印（いま動かしている所）が出ます。スクリーンを見ながら十字ボタンで合わせてください。図の角や内側を指でなぞっても動かせます。</div>
</main>
<div id="toast"></div>
<script>
const $=id=>document.getElementById(id);
let S=null, lastOk=0, fast=0, drag=null, step=1, lastSend=0;
const LCOL=['#ff5a5a','#59ccff','#73ff73','#ffd94d','#d980ff','#4dffe6'];
function post(c){return fetch('/api/cmd',{method:'POST',body:JSON.stringify(c)});}
function cmd(c){fast=Date.now()+2500; post(c).then(()=>setTimeout(poll,80)).catch(()=>{});}
function toast(t){const e=$('toast'); e.textContent=t; e.style.display='block'; clearTimeout(e._t); e._t=setTimeout(()=>e.style.display='none',2500);}
function L(){return S&&S.layers[S.selected];}
function place(){const l=L(); return l&&l.places.find(p=>p.output===S.map_out);}

// ---- 状態を取りに行く（ふだん0.3秒、触っている間は速く）----
async function poll(){
  try{
    const ctl=new AbortController(), t=setTimeout(()=>ctl.abort(),2500);
    const r=await fetch('/api/mstate',{cache:'no-store',signal:ctl.signal}); clearTimeout(t);
    S=await r.json(); lastOk=Date.now(); render();
  }catch(e){}
}
function loop(){poll(); setTimeout(loop,Date.now()<fast?120:300);}
setInterval(()=>{const ok=Date.now()-lastOk<3000; document.body.classList.toggle('lost',!ok); $('dot').classList.toggle('ok',ok);},500);

let lkey='';
function render(){
  if(!S||!S.layers)return;
  const k=JSON.stringify([S.layers.map(l=>[l.id,l.name]),S.selected]);
  if(k!==lkey){lkey=k; $('layer').innerHTML=S.layers.map((l,i)=>`<option value="${i}" ${i===S.selected?'selected':''}>L${i+1} ${esc(l.name)}${l.kind==='photo'?'（写真）':''}</option>`).join('');}
  const l=L(), pls=l?l.places:[];
  $('outrow').style.display=pls.length>1?'flex':'none';
  const ok=JSON.stringify([pls.map(p=>p.output),S.map_out]);
  if($('out').dataset.k!==ok){$('out').dataset.k=ok;
    $('out').innerHTML=pls.map(p=>`<option value="${p.output}" ${p.output===S.map_out?'selected':''}>${esc((S.targets.find(t=>t.output===p.output)||{}).name||('出力'+(p.output+1)))}</option>`).join('');}
  $('edit').classList.toggle('on',S.edit); $('edit').textContent=S.edit?'枠を表示中':'枠を表示';
  document.querySelectorAll('[data-c]').forEach(b=>b.classList.toggle('on',+b.dataset.c===S.sel_corner));
  $('test').classList.toggle('held',!!(l&&l.test));
  $('undo').disabled=!S.undo;
  if(l){ if(document.activeElement!==$('op')) $('op').value=l.opacity; if(document.activeElement!==$('fe')) $('fe').value=l.feather;
    $('opv').textContent=Math.round(l.opacity*100)+'%'; $('fev').textContent=Math.round(l.feather*100);}
  if(S.message&&S.message!==render._m){render._m=S.message; toast(S.message);}
  draw();
}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}

// ---- 配置図 ----
function draw(){
  const cv=$('map'), [tw,th]=S.size||[1920,1080];
  const W=cv.clientWidth*devicePixelRatio, H=Math.round(W*th/tw);
  if(cv.width!==W||cv.height!==H){cv.width=W; cv.height=H;}
  const g=cv.getContext('2d'); g.clearRect(0,0,W,H);
  g.strokeStyle='#222'; g.lineWidth=1;
  for(let i=1;i<4;i++){g.beginPath();g.moveTo(W*i/4,0);g.lineTo(W*i/4,H);g.moveTo(0,H*i/4);g.lineTo(W,H*i/4);g.stroke();}
  S.layers.forEach((l,i)=>{
    const p=l.places.find(p=>p.output===S.map_out); if(!p)return;
    const sel=i===S.selected, c=(drag&&sel)?drag.pts:p.corners, col=LCOL[i%LCOL.length];
    g.globalAlpha=sel?1:.35; g.strokeStyle=col; g.fillStyle=col+(sel?'26':'10'); g.lineWidth=(sel?3:1.5)*devicePixelRatio;
    g.beginPath(); c.forEach((q,k)=>k?g.lineTo(q[0]*W,q[1]*H):g.moveTo(q[0]*W,q[1]*H)); g.closePath(); g.fill(); g.stroke();
    if(sel){
      if(S.sel_corner===4){g.strokeStyle='#ff4d4f'; g.lineWidth=4*devicePixelRatio; g.stroke();}
      c.forEach((q,k)=>{const r=11*devicePixelRatio, x=Math.min(W-r,Math.max(r,q[0]*W)), y=Math.min(H-r,Math.max(r,q[1]*H));
        g.fillStyle=k===S.sel_corner?'#ff4d4f':'#fff'; g.fillRect(x-r,y-r,2*r,2*r); g.strokeStyle=col; g.lineWidth=2*devicePixelRatio; g.strokeRect(x-r,y-r,2*r,2*r);});
    }
    g.globalAlpha=1;
  });
}
function mpos(e){const r=$('map').getBoundingClientRect(); return [(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height];}
function inside(poly,x,y){let o=false;for(let i=0,j=3;i<4;j=i++){const[a,b]=poly[i],[c,d]=poly[j];if((b>y)!==(d>y)&&x<(c-a)*(y-b)/(d-b+1e-12)+a)o=!o;}return o;}
$('map').addEventListener('pointerdown',e=>{
  const p=place(); if(!p)return; const [x,y]=mpos(e), cv=$('map');
  let best=-1,bd=1e9; p.corners.forEach((q,k)=>{const d=Math.hypot((q[0]-x)*cv.clientWidth,(q[1]-y)*cv.clientHeight); if(d<bd){bd=d;best=k;}});
  const pts=p.corners.map(q=>q.slice()), lid=L().id, o=S.map_out;
  if(bd<34) drag={k:best,pts,lid,o}; else if(inside(pts,x,y)) drag={k:-1,pts,lid,o,last:[x,y]}; else return;
  S.sel_corner=bd<34?best:4; post({cmd:'sel_corner',value:S.sel_corner});
  cv.setPointerCapture(e.pointerId); e.preventDefault(); render();
});
$('map').addEventListener('pointermove',e=>{
  if(!drag)return; const [x,y]=mpos(e);
  if(drag.k>=0) drag.pts[drag.k]=[x,y]; else{const dx=x-drag.last[0],dy=y-drag.last[1]; drag.pts.forEach(q=>{q[0]+=dx;q[1]+=dy;}); drag.last=[x,y];}
  draw(); const t=performance.now(); if(t-lastSend>60){lastSend=t; sendDrag();}
});
function sendDrag(){const k=S.layers.findIndex(l=>l.id===drag.lid); if(k>=0) post({cmd:'corners',layer:k,output:drag.o,value:drag.pts});}
function endDrag(){ if(!drag)return; sendDrag(); const p=place(); if(p) p.corners=drag.pts; drag=null; fast=Date.now()+2500; setTimeout(poll,100);}
['pointerup','pointercancel','lostpointercapture'].forEach(n=>$('map').addEventListener(n,endDrag));

// ---- ボタン ----
$('layer').onchange=()=>cmd({cmd:'select',layer:+$('layer').value});
$('out').onchange=()=>cmd({cmd:'map_out',output:+$('out').value});
$('edit').onclick=()=>cmd({cmd:'edit',value:!S.edit});
document.querySelectorAll('[data-c]').forEach(b=>b.onclick=()=>{S.sel_corner=+b.dataset.c; render(); cmd({cmd:'sel_corner',value:S.sel_corner});});
$('step').onclick=()=>{step=step===1?10:1; $('step').textContent=step+'px';};
$('undo').onclick=()=>cmd({cmd:'undo_corners'});
$('test').onclick=()=>{const l=L(); if(l) cmd({cmd:'set',layer:S.selected,key:'test',value:!l.test});};
$('reset').onclick=()=>{ if(confirm('このレイヤーの四隅を、画面いっぱいの四角に戻しますか？（「1つ戻す」で元に戻せます）')) cmd({cmd:'reset_corners',layer:S.selected,output:S.map_out});};
function slider(id,key){let t=0; $(id).addEventListener('input',()=>{const n=performance.now(); if(n-t<80)return; t=n; post({cmd:'set',layer:S.selected,key,value:parseFloat($(id).value)});});
  $(id).addEventListener('change',()=>cmd({cmd:'set',layer:S.selected,key,value:parseFloat($(id).value)}));}
slider('op','opacity'); slider('fe','feather');
// 十字ボタン：押す＝1回、長押し＝続けて（1.5秒たつと速くなる）
document.querySelectorAll('[data-d]').forEach(b=>{
  let t1=null,t2=null,t0=0;
  const [dx,dy]=b.dataset.d.split(',').map(Number);
  const go=()=>{const n=Date.now()-t0>1500?step*5:step; post({cmd:'nudge',dx,dy,step:n}); fast=Date.now()+2500;};
  const stop=()=>{clearTimeout(t1); clearInterval(t2); t1=t2=null; setTimeout(poll,80);};
  b.addEventListener('pointerdown',e=>{e.preventDefault(); b.setPointerCapture(e.pointerId); t0=Date.now(); go();
    t1=setTimeout(()=>{t2=setInterval(go,70);},350);});
  ['pointerup','pointercancel','lostpointercapture'].forEach(n=>b.addEventListener(n,stop));
});
document.addEventListener('contextmenu',e=>e.preventDefault());  // 長押しでメニューが出ないように
// スマホでもキーボードがつながっていれば矢印キーで
document.addEventListener('keydown',e=>{const d={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];
  if(d){e.preventDefault(); post({cmd:'nudge',dx:d[0],dy:d[1],step:e.shiftKey?10:step}); fast=Date.now()+2500;}});
window.addEventListener('resize',()=>S&&draw());
loop();
// 開いたらスクリーンに枠を出す（位置合わせのための画面なので）
setTimeout(()=>{ if(S&&!S.edit) cmd({cmd:'edit',value:true}); },700);
</script></body></html>
"""
