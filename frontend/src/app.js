import {api,post,session,download} from './api/client.js';
import {batches,mappings,runs,billing} from './api/business.js';
import {store,refresh} from './stores/business.js';
import {login} from './pages/login.js';
import {batchesPage} from './pages/batches.js';
import {mappingsPage} from './pages/mappings.js';
import {runsPage} from './pages/runs.js';
import {resultsPage} from './pages/results.js';
import {billingPage,loadDetail} from './pages/billing.js';
import {routes} from './router/routes.js';
import {ROLES} from './constants/enums.js';
import {esc} from './utils/html.js';
import {feedback} from './components/common/feedback.js';
import {submit} from './hooks/forms.js';
import {poll} from './hooks/poll.js';

let page='batches',resultId=null;const app=document.getElementById('app');
function render(){if(!store.user){app.innerHTML=login();bind();return;}let title=routes.find(r=>r[0]===page)?.[2]||'审计记录';app.innerHTML=`<aside class="sidebar"><a class="brand" href="#">见山<span>ARCHIVE TRANSFER / 09</span></a><p>检验结果<br>迁移核对站</p><nav>${routes.map(([key,num,name])=>`<button data-page="${key}" class="${key===page?'active':''}"><small>${num}</small>${name}</button>`).join('')}${store.user.role==='admin'?'<button data-page="audit"><small>06</small>审计记录</button>':''}</nav><div class="identity"><b>${ROLES[store.user.role]}</b><small>${esc(store.user.email)}</small><button class="quiet" id="logout">退出登录 ↗</button></div></aside><main class="workspace"><header><span class="eyebrow">QUALITY ARCHIVE · HANDOVER RECEIPT</span><span class="live">PostgreSQL / 原子提交目标行</span></header><div class="page-title"><span class="eyebrow">TRANSFER LEDGER / 2026.11</span><h1>${title}</h1><p>把旧字段交给新结构，带着每一行的去向与校验凭据。</p></div><div id="feedback" class="feedback" hidden></div><div id="content">${page==='batches'?batchesPage():page==='mappings'?mappingsPage():page==='runs'?runsPage():page==='results'?resultsPage(resultId):page==='billing'?billingPage():'<pre id="audit-data">正在读取…</pre>'}</div><footer>见山质量档案组 / 受控字段迁移演练与逻辑JSON备份，不连接生产数据库。</footer></main>`;bind();if(page==='audit')api('/audit').then(x=>document.getElementById('audit-data').textContent=JSON.stringify(x,null,2)).catch(e=>feedback(e.message,true));if(store.user.role==='viewer')document.querySelectorAll('form button[type=submit], [data-edit-batch], [data-edit-mapping]').forEach(b=>{b.disabled=true;b.title='观察员只读';});if(store.user.role!=='admin')document.querySelectorAll('[data-bill-reopen]').forEach(b=>b.remove());}

function bind(){
 document.getElementById('login-form')?.addEventListener('submit',async e=>{e.preventDefault();try{await submit(e.target,async d=>{const r=await post('/auth/login',d);session.token=r.token;store.user=r.user;await refresh();render();});}catch(e){feedback(e.message,true);}});
 document.getElementById('logout')?.addEventListener('click',()=>{session.token=null;store.user=null;render();});
 document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{page=b.dataset.page;render();window.scrollTo(0,0);});
 document.querySelectorAll('[data-reset]').forEach(b=>b.onclick=()=>{document.getElementById(b.dataset.reset).reset();document.querySelector('#'+b.dataset.reset+' [name=id]').value='';});
 document.querySelectorAll('[data-edit-batch]').forEach(b=>b.onclick=()=>{let d=store.batches.find(x=>x.id===Number(b.dataset.editBatch));fill('batch-form',{...d,rows:JSON.stringify(d.rows,null,2)});});
 document.querySelectorAll('[data-edit-mapping]').forEach(b=>b.onclick=()=>{let m=store.mappings.find(x=>x.id===Number(b.dataset.editMapping));fill('mapping-form',{...m,rules:JSON.stringify(m.rules,null,2)});});
 document.querySelectorAll('[data-result]').forEach(b=>b.onclick=()=>{resultId=b.dataset.result;page='results';render();window.scrollTo(0,0);});
 document.querySelectorAll('[data-export]').forEach(b=>b.onclick=()=>download(b.dataset.export).catch(e=>feedback(e.message,true)));
 document.querySelectorAll('[data-backup]').forEach(b=>b.onclick=()=>download(b.dataset.backup,'backup').catch(e=>feedback(e.message,true)));
 document.querySelectorAll('[data-cancel]').forEach(b=>b.onclick=async()=>{try{await runs.cancel(b.dataset.cancel);await refresh();render();feedback('已发出取消请求；尚未提交的目标行将全部丢弃。');}catch(e){feedback(e.message,true);}});
 document.querySelectorAll('[data-targets]').forEach(b=>b.onclick=async()=>{try{let data=await runs.targets(b.dataset.targets);let el=document.getElementById('target-data');el.hidden=false;el.textContent=JSON.stringify(data,null,2);}catch(e){feedback(e.message,true);}});
 document.querySelectorAll('[data-restore]').forEach(b=>b.onclick=async()=>{try{let file=document.getElementById('backup-file').files[0];if(!file)throw Error('请先选择本运行下载的JSON备份');let result=await runs.restore(b.dataset.restore,JSON.parse(await file.text()));await refresh();render();feedback('已恢复'+result.restored_count+'行，校验'+(result.verified?'一致':'失败')+'；恢复不重复计费。');}catch(e){feedback(e.message,true);}});
 document.querySelectorAll('[data-bill-month]').forEach(b=>b.onclick=async()=>{try{b.disabled=true;await loadDetail(b.dataset.billMonth);}catch(e){feedback(e.message,true);}finally{b.disabled=false;}
  document.getElementById('billing-detail')?.scrollIntoView({behavior:'smooth',block:'start'});});
 document.querySelectorAll('[data-bill-reopen]').forEach(b=>b.onclick=async()=>{try{await billing.reopen(b.dataset.billReopen);await refresh();render();feedback('已解封 '+b.dataset.billReopen+'，可以重新入库与封账。');}catch(e){feedback(e.message,true);}});
 document.getElementById('billing-toggle')?.addEventListener('click',async e=>{try{let want=!(store.billing?.enabled);await billing.toggle(want);await refresh();render();feedback(want?'已启用计件；此后首次成功入库计费。':'已暂停计件；入库仍登记，但不再产生费用。');}catch(err){feedback(err.message,true);}});
 document.getElementById('billing-backfill')?.addEventListener('click',async e=>{try{e.target.disabled=true;let r=await billing.backfill();await refresh();render();feedback(r.already_backfilled?'历史入库已回填过，无需重复。':('回填完成：登记 '+r.registered+' 条，未计费 '+r.skipped+' 条。'));}catch(err){feedback(err.message,true);}});
 const handlers={
  'batch-form':async d=>{const id=d.id;delete d.id;d.rows=JSON.parse(d.rows);await batches.save(d,id);},
  'mapping-form':async d=>{const id=d.id;delete d.id;d.batch_id=Number(d.batch_id);d.chunk_size=Number(d.chunk_size);d.rules=JSON.parse(d.rules);await mappings.save(d,id);},
  'run-form':async d=>{d.mapping_id=Number(d.mapping_id);let r=await runs.run(d);resultId=r.id;}};
 for(const [id,fn] of Object.entries(handlers))document.getElementById(id)?.addEventListener('submit',async e=>{e.preventDefault();try{await submit(e.target,fn);await refresh();render();feedback('已保存。到迁移执行和差异核对页复查进展。');}catch(e){feedback(e.message,true);}});
 const closeForm=document.getElementById('close-form');
 closeForm?.addEventListener('submit',async e=>{e.preventDefault();try{await submit(closeForm,async d=>{const r=await billing.close(d.month.trim(),d.note);await refresh();render();feedback('已封账 '+r.month+'：冻结 '+r.billed_count+' 笔计件（序号 '+r.close_seq+'）。'+(r.pending_runs.length?('另有 '+r.pending_runs.length+' 个先于本次封账提交的运行尚未入库：若其检测月为 '+r.month+'，将保留原月并标注需复核。'):''));});}catch(err){feedback(err.message,true);}});
}
function fill(id,data){const form=document.getElementById(id);for(let [k,v] of Object.entries(data)){let f=form.elements[k];if(f)f.value=v??'';}form.scrollIntoView({behavior:'smooth',block:'center'});}
try{if(session.token){store.user=await api('/auth/me');await refresh();}}catch(e){session.token=null;store.user=null;}render();poll(async()=>{if(!store.user)return;try{await refresh();if(['results','runs'].includes(page)&&!document.querySelector('input:focus,textarea:focus,select:focus')&&!document.getElementById('backup-file')?.files.length)render();}catch(e){feedback(e.message,true);}});
