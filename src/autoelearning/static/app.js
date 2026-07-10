const state={data:null,view:'overview',assignmentFilter:'all'};
const $=s=>document.querySelector(s);const $$=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const fmtDate=v=>v?new Intl.DateTimeFormat('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(v)):'无截止时间';
const shortDate=v=>v?{day:new Date(v).getDate(),month:new Intl.DateTimeFormat('en',{month:'short'}).format(new Date(v))}:{day:'—',month:'OPEN'};
const isDone=a=>['submitted','graded'].includes(a.submission_state);const isPending=a=>!isDone(a);
async function api(url,options={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...options});const d=await r.json();if(!r.ok)throw new Error(d.error||'请求失败');return d}
function banner(message,error=false){const el=$('#status-banner');el.textContent=message;el.className='status-banner'+(error?' error':'');setTimeout(()=>el.classList.add('hidden'),5000)}
function showLogin(show=true){$('#login-modal').classList.toggle('hidden',!show)}
async function init(){
  $('#today').textContent=new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'long'}).format(new Date());
  bindEvents();await loadDashboard();
  try{const s=await api('/api/session');if(s.authenticated){setUser(s.user);showLogin(false)}else showLogin(true)}catch{showLogin(true)}
}
function bindEvents(){
  $$('#nav .nav-item').forEach(b=>b.onclick=()=>switchView(b.dataset.view));$$('[data-jump]').forEach(b=>b.onclick=()=>switchView(b.dataset.jump));
  $('#login-form').onsubmit=login;$('#sync-button').onclick=sync;$('#user-chip').onclick=()=>showLogin(true);
  $('#assignment-search').oninput=renderAssignments;$('#announcement-search').oninput=renderAnnouncements;$('#material-search').oninput=renderMaterials;
  $$('.segmented button').forEach(b=>b.onclick=()=>{$$('.segmented button').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.assignmentFilter=b.dataset.filter;renderAssignments()});
}
async function login(e){e.preventDefault();const button=$('#login-button'),error=$('#login-error');button.disabled=true;button.textContent='正在连接…';error.classList.add('hidden');
  try{const d=await api('/api/login',{method:'POST',body:JSON.stringify({username:$('#username').value,password:$('#password').value})});setUser(d.user);showLogin(false);banner('登录成功，正在同步课程数据。');await sync()}
  catch(err){error.textContent=err.message;error.classList.remove('hidden')}finally{button.disabled=false;button.textContent='登录并连接'}
}
async function sync(){const b=$('#sync-button');b.disabled=true;b.classList.add('syncing');b.innerHTML='<span class="sync-icon">↻</span>正在同步';
  try{const d=await api('/api/sync',{method:'POST',body:'{}'});await loadDashboard();banner(`同步完成：${d.assignments} 条作业，${d.announcements} 条公告，${d.materials} 项课件。`)}
  catch(err){banner(err.message,true);if(/登录|认证|会话/.test(err.message))showLogin(true)}finally{b.disabled=false;b.classList.remove('syncing');b.innerHTML='<span class="sync-icon">↻</span>同步 eLearning'}
}
async function loadDashboard(){try{state.data=await api('/api/dashboard');renderAll()}catch(err){banner(err.message,true)}}
function setUser(u){$('#user-name').textContent=u.name||u.short_name||'已登录';$('.avatar').textContent=(u.name||'同').slice(0,1);$('#user-chip').classList.remove('hidden')}
function switchView(view){state.view=view;$$('.view').forEach(v=>v.classList.toggle('active-view',v.id===view));$$('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$('#page-title').textContent={overview:'学习总览',assignments:'作业中心',announcements:'课程公告',materials:'课件资料',courses:'我的课程'}[view]}
function renderAll(){if(!state.data)return;const d=state.data;$('#stat-assignments').textContent=d.counts.assignments;$('#stat-pending').textContent=d.assignments.filter(isPending).length;$('#stat-announcements').textContent=d.counts.announcements;$('#stat-materials').textContent=d.counts.materials;renderOverview();renderAssignments();renderAnnouncements();renderMaterials();renderCourses()}
function renderOverview(){const d=state.data;const upcoming=[...d.assignments].filter(a=>a.due_at&&new Date(a.due_at)>new Date()).sort((a,b)=>new Date(a.due_at)-new Date(b.due_at)).slice(0,5);const fallback=d.assignments.slice(0,5);const assignments=upcoming.length?upcoming:fallback;
  $('#recent-assignments').classList.remove('loading-list');$('#recent-assignments').innerHTML=assignments.length?assignments.map(a=>{const dt=shortDate(a.due_at);return `<div class="list-item"><div class="date-block"><strong>${dt.day}</strong><span>${dt.month}</span></div><div class="item-copy"><strong>${esc(a.name)}</strong><span>${esc(a.course_code||a.course_name)}</span></div><span class="badge ${isDone(a)?'done':'pending'}">${isDone(a)?'已完成':'待提交'}</span></div>`}).join(''):'<div class="empty">暂无作业数据</div>';
  const anns=d.announcements.slice(0,5);$('#recent-announcements').classList.remove('loading-list');$('#recent-announcements').innerHTML=anns.length?anns.map(a=>{const dt=shortDate(a.posted_at);return `<div class="list-item"><div class="date-block"><strong>${dt.day}</strong><span>${dt.month}</span></div><div class="item-copy"><strong>${esc(a.title)}</strong><span>${esc(a.course_code||a.course_name)}</span></div><a class="row-link" href="${esc(a.html_url||'#')}" target="_blank">打开</a></div>`}).join(''):'<div class="empty">暂无公告数据</div>'}
function renderAssignments(){if(!state.data)return;const q=$('#assignment-search').value.trim().toLowerCase();let rows=state.data.assignments.filter(a=>(a.name+' '+a.course_name).toLowerCase().includes(q));if(state.assignmentFilter==='pending')rows=rows.filter(isPending);if(state.assignmentFilter==='done')rows=rows.filter(isDone);
  $('#assignment-table').innerHTML=`<div class="table-row header"><span>作业</span><span>课程</span><span>截止时间</span><span>状态</span></div>`+(rows.length?rows.map(a=>`<div class="table-row"><div><a class="row-link row-title" href="${esc(a.html_url||'#')}" target="_blank">${esc(a.name)}</a><div class="row-sub">作业编号 ${a.id}</div></div><div>${esc(a.course_code||a.course_name)}</div><div>${fmtDate(a.due_at)}</div><div><span class="badge ${isDone(a)?'done':'pending'}">${isDone(a)?(a.submission_state==='graded'?'已评分':'已提交'):'待提交'}</span></div></div>`).join(''):'<div class="empty">没有符合条件的作业</div>')}
function renderAnnouncements(){if(!state.data)return;const q=$('#announcement-search').value.trim().toLowerCase();const rows=state.data.announcements.filter(a=>(a.title+' '+a.course_name).toLowerCase().includes(q));$('#announcement-list').innerHTML=rows.length?rows.map(a=>`<article class="announcement-card"><p class="eyebrow">${esc(a.course_code||'COURSE')}</p><h3>${esc(a.title)}</h3><div class="announcement-meta"><span>${fmtDate(a.posted_at)}</span><a class="row-link" href="${esc(a.html_url||'#')}" target="_blank">查看原文 →</a></div></article>`).join(''):'<div class="empty">暂无公告</div>'}
function renderMaterials(){if(!state.data)return;const q=$('#material-search').value.trim().toLowerCase();const rows=state.data.materials.filter(m=>(m.title+' '+m.course_name).toLowerCase().includes(q));$('#material-list').innerHTML=`<div class="table-row header"><span>文件</span><span>课程</span><span>类型</span><span>操作</span></div>`+(rows.length?rows.map(m=>`<div class="table-row"><div><strong class="row-title">${esc(m.title)}</strong><div class="row-sub">${esc(m.module_name||'课程文件')}</div></div><div>${esc(m.course_code||m.course_name)}</div><div>${esc(m.kind)}</div><div><a class="row-link" href="${esc(m.url||'#')}" target="_blank">下载 ↗</a></div></div>`).join(''):'<div class="empty">暂无课件文件</div>')}
function renderCourses(){if(!state.data)return;$('#course-grid').innerHTML=state.data.courses.length?state.data.courses.map((c,i)=>`<article class="course-card" style="--blue:${['#315e93','#a82934','#257a79','#9a6a25'][i%4]}"><div><p class="course-code">${esc(c.course_code||'COURSE')}</p><h3>${esc(c.name)}</h3></div><span>课程编号 ${c.id}</span></article>`).join(''):'<div class="empty">暂无课程数据</div>'}
init();
