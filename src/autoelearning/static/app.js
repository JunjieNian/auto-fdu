const state = {
  data: null,
  view: "overview",
  assignmentFilter: "all",
  currentJob: null,
  approvalToken: null,
  pollTimer: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[char]);
const fmtDate = (value) => value
  ? new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value))
  : "无截止时间";
const shortDate = (value) => value
  ? { day: new Date(value).getDate(), month: new Intl.DateTimeFormat("en", { month: "short" }).format(new Date(value)) }
  : { day: "—", month: "OPEN" };
const isDone = (assignment) => ["submitted", "graded"].includes(assignment.submission_state);
const isPending = (assignment) => !isDone(assignment);

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function banner(message, error = false) {
  const element = $("#status-banner");
  element.textContent = message;
  element.className = `status-banner${error ? " error" : ""}`;
  setTimeout(() => element.classList.add("hidden"), 6000);
}

function showLogin(show = true) { $("#login-modal").classList.toggle("hidden", !show); }
function showReview(show = true) { $("#review-modal").classList.toggle("hidden", !show); }

async function init() {
  $("#today").textContent = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "long", day: "numeric", weekday: "long",
  }).format(new Date());
  bindEvents();
  await loadDashboard();
  try {
    const session = await api("/api/session");
    if (session.authenticated) {
      setUser(session.user);
      showLogin(false);
    } else showLogin(true);
  } catch { showLogin(true); }
}

function bindEvents() {
  $$("#nav .nav-item").forEach((button) => { button.onclick = () => switchView(button.dataset.view); });
  $$("[data-jump]").forEach((button) => { button.onclick = () => switchView(button.dataset.jump); });
  $("#login-form").onsubmit = login;
  $("#sync-button").onclick = sync;
  $("#user-chip").onclick = () => showLogin(true);
  $("#assignment-search").oninput = renderAssignments;
  $("#announcement-search").oninput = renderAnnouncements;
  $("#material-search").oninput = renderMaterials;
  $$(".segmented button").forEach((button) => {
    button.onclick = () => {
      $$(".segmented button").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.assignmentFilter = button.dataset.filter;
      renderAssignments();
    };
  });
  $("#review-close").onclick = () => showReview(false);
  $("#save-draft-button").onclick = saveReviewedDraft;
  $("#approve-button").onclick = approveCurrentJob;
  $("#final-submit-button").onclick = submitCurrentJob;
}

async function login(event) {
  event.preventDefault();
  const button = $("#login-button");
  const error = $("#login-error");
  button.disabled = true;
  button.textContent = "正在连接…";
  error.classList.add("hidden");
  try {
    const data = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ username: $("#username").value, password: $("#password").value }),
    });
    setUser(data.user);
    showLogin(false);
    banner("登录成功，正在同步课程数据。");
    await sync();
  } catch (err) {
    error.textContent = err.message;
    error.classList.remove("hidden");
  } finally {
    button.disabled = false;
    button.textContent = "登录并连接";
  }
}

async function sync() {
  const button = $("#sync-button");
  button.disabled = true;
  button.classList.add("syncing");
  button.innerHTML = '<span class="sync-icon">↻</span>正在同步';
  try {
    const data = await api("/api/sync", { method: "POST", body: "{}" });
    await loadDashboard();
    banner(`同步完成：${data.assignments} 条作业，${data.announcements} 条公告，${data.materials} 项课件。`);
  } catch (err) {
    banner(err.message, true);
    if (/登录|认证|会话/.test(err.message)) showLogin(true);
  } finally {
    button.disabled = false;
    button.classList.remove("syncing");
    button.innerHTML = '<span class="sync-icon">↻</span>同步 eLearning';
  }
}

async function loadDashboard() {
  try {
    state.data = await api("/api/dashboard");
    renderAll();
  } catch (err) { banner(err.message, true); }
}

function setUser(user) {
  $("#user-name").textContent = user.name || user.short_name || "已登录";
  $(".avatar").textContent = (user.name || "同").slice(0, 1);
  $("#user-chip").classList.remove("hidden");
}

function switchView(view) {
  state.view = view;
  $$(".view").forEach((item) => item.classList.toggle("active-view", item.id === view));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $("#page-title").textContent = {
    overview: "学习总览", assignments: "作业中心", announcements: "课程公告",
    materials: "课件资料", courses: "我的课程", agent: "Agent 工作台",
  }[view];
}

function renderAll() {
  if (!state.data) return;
  const data = state.data;
  $("#stat-assignments").textContent = data.counts.assignments;
  $("#stat-pending").textContent = data.assignments.filter(isPending).length;
  $("#stat-announcements").textContent = data.counts.announcements;
  $("#stat-materials").textContent = data.counts.materials;
  renderOverview();
  renderAssignments();
  renderAnnouncements();
  renderMaterials();
  renderCourses();
  renderAgent();
}

function renderOverview() {
  const data = state.data;
  const upcoming = [...data.assignments]
    .filter((item) => item.due_at && new Date(item.due_at) > new Date())
    .sort((a, b) => new Date(a.due_at) - new Date(b.due_at)).slice(0, 5);
  const assignments = upcoming.length ? upcoming : data.assignments.slice(0, 5);
  $("#recent-assignments").classList.remove("loading-list");
  $("#recent-assignments").innerHTML = assignments.length ? assignments.map((item) => {
    const date = shortDate(item.due_at);
    return `<div class="list-item"><div class="date-block"><strong>${date.day}</strong><span>${date.month}</span></div><div class="item-copy"><strong>${esc(item.name)}</strong><span>${esc(item.course_code || item.course_name)}</span></div><span class="badge ${isDone(item) ? "done" : "pending"}">${isDone(item) ? "已完成" : "待提交"}</span></div>`;
  }).join("") : '<div class="empty">暂无作业数据</div>';
  const announcements = data.announcements.slice(0, 5);
  $("#recent-announcements").classList.remove("loading-list");
  $("#recent-announcements").innerHTML = announcements.length ? announcements.map((item) => {
    const date = shortDate(item.posted_at);
    return `<div class="list-item"><div class="date-block"><strong>${date.day}</strong><span>${date.month}</span></div><div class="item-copy"><strong>${esc(item.title)}</strong><span>${esc(item.course_code || item.course_name)}</span></div><a class="row-link" href="${esc(item.html_url || "#")}" target="_blank">打开</a></div>`;
  }).join("") : '<div class="empty">暂无公告数据</div>';
}

function renderAssignments() {
  if (!state.data) return;
  const query = $("#assignment-search").value.trim().toLowerCase();
  let rows = state.data.assignments.filter((item) => `${item.name} ${item.course_name}`.toLowerCase().includes(query));
  if (state.assignmentFilter === "pending") rows = rows.filter(isPending);
  if (state.assignmentFilter === "done") rows = rows.filter(isDone);
  $("#assignment-table").innerHTML = '<div class="table-row header"><span>作业</span><span>课程</span><span>截止时间</span><span>状态</span></div>' + (rows.length ? rows.map((item) => `<div class="table-row"><div><a class="row-link row-title" href="${esc(item.html_url || "#")}" target="_blank">${esc(item.name)}</a><div class="row-sub">作业编号 ${item.id}</div></div><div>${esc(item.course_code || item.course_name)}</div><div>${fmtDate(item.due_at)}</div><div><span class="badge ${isDone(item) ? "done" : "pending"}">${isDone(item) ? (item.submission_state === "graded" ? "已评分" : "已提交") : "待提交"}</span></div></div>`).join("") : '<div class="empty">没有符合条件的作业</div>');
}

function renderAnnouncements() {
  if (!state.data) return;
  const query = $("#announcement-search").value.trim().toLowerCase();
  const rows = state.data.announcements.filter((item) => `${item.title} ${item.course_name}`.toLowerCase().includes(query));
  $("#announcement-list").innerHTML = rows.length ? rows.map((item) => `<article class="announcement-card"><p class="eyebrow">${esc(item.course_code || "COURSE")}</p><h3>${esc(item.title)}</h3><div class="announcement-meta"><span>${fmtDate(item.posted_at)}</span><a class="row-link" href="${esc(item.html_url || "#")}" target="_blank">查看原文 →</a></div></article>`).join("") : '<div class="empty">暂无公告</div>';
}

function renderMaterials() {
  if (!state.data) return;
  const query = $("#material-search").value.trim().toLowerCase();
  const rows = state.data.materials.filter((item) => `${item.title} ${item.course_name}`.toLowerCase().includes(query));
  $("#material-list").innerHTML = '<div class="table-row header"><span>文件</span><span>课程</span><span>类型</span><span>操作</span></div>' + (rows.length ? rows.map((item) => `<div class="table-row"><div><strong class="row-title">${esc(item.title)}</strong><div class="row-sub">${esc(item.module_name || "课程文件")}</div></div><div>${esc(item.course_code || item.course_name)}</div><div>${esc(item.kind)}</div><div><a class="row-link" href="${esc(item.url || "#")}" target="_blank">下载 ↗</a></div></div>`).join("") : '<div class="empty">暂无课件文件</div>');
}

function renderCourses() {
  if (!state.data) return;
  $("#course-grid").innerHTML = state.data.courses.length ? state.data.courses.map((course, index) => `<article class="course-card" style="--blue:${["#315e93", "#a82934", "#257a79", "#9a6a25"][index % 4]}"><div><p class="course-code">${esc(course.course_code || "COURSE")}</p><h3>${esc(course.name)}</h3></div><span>课程编号 ${course.id}</span></article>`).join("") : '<div class="empty">暂无课程数据</div>';
}

function renderAgent() {
  if (!state.data) return;
  const capabilities = state.data.agent || {};
  const locked = !capabilities.submission_enabled;
  $("#submission-lock-badge").textContent = locked ? "提交已锁定" : "提交已启用";
  $("#submission-lock-badge").className = `badge ${locked ? "done" : "pending"}`;
  $("#agent-runtime").textContent = capabilities.codex_available ? (capabilities.busy ? "Agent 运行中" : "Codex 已就绪") : "Codex 不可用";
  const assignments = [...state.data.assignments].sort((a, b) => Number(isDone(a)) - Number(isDone(b)));
  $("#agent-assignment-list").innerHTML = assignments.length ? assignments.map((item) => {
    const completed = isDone(item);
    return `<div class="agent-assignment"><div><h4>${esc(item.name)}</h4><p>${esc(item.course_code || item.course_name)} · ${fmtDate(item.due_at)}${completed ? " · 已完成测试模式" : ""}</p></div><button class="small-button" data-create-draft="${item.id}" data-test-mode="${completed}" ${(!capabilities.codex_available || capabilities.busy) ? "disabled" : ""}>${completed ? "测试生成（不可提交）" : "生成审查草稿"}</button></div>`;
  }).join("") : '<div class="empty">没有作业数据</div>';
  $$('[data-create-draft]').forEach((button) => { button.onclick = () => createDraft(Number(button.dataset.createDraft), button.dataset.testMode === "true"); });
  const jobs = state.data.agent_jobs || [];
  $("#agent-job-list").innerHTML = jobs.length ? jobs.map((job) => `<div class="agent-job"><div class="job-top"><div><h4>${esc(job.assignment_name)}</h4><p>${esc(job.course_code || job.course_name)}${job.test_mode ? " · 永久不可提交" : ""}</p></div><span class="job-status ${esc(job.status)}">${esc(jobStatusText(job.status))}</span></div><div class="job-meta"><span>${fmtDate(job.updated_at)}</span>${["draft_ready", "approved", "failed", "submit_failed"].includes(job.status) ? `<button class="small-button" data-review-job="${job.id}">查看与审查</button>` : ""}</div>${job.error ? `<p class="form-error">${esc(job.error)}</p>` : ""}</div>`).join("") : '<div class="empty">还没有 Agent 草稿</div>';
  $$('[data-review-job]').forEach((button) => { button.onclick = () => openReview(Number(button.dataset.reviewJob)); });
}

function jobStatusText(status) {
  return {
    queued: "排队中", preparing: "收集资料", running: "生成中", draft_ready: "待审查",
    approved: "已批准", failed: "生成失败", submitting: "提交中", submitted: "已提交",
    submit_failed: "提交失败",
  }[status] || status;
}

async function createDraft(assignmentId, testMode = false) {
  try {
    const result = await api("/api/agent/jobs", { method: "POST", body: JSON.stringify({ assignment_id: assignmentId, test_mode: testMode }) });
    banner(testMode ? "测试模式 Agent 已启动：将生成草稿和 PDF，但永久禁止提交。" : "Agent 已启动，正在收集题目、附件和相关课件。");
    await loadDashboard();
    startPolling(result.job_id);
  } catch (err) { banner(err.message, true); }
}

function startPolling(jobId) {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    await loadDashboard();
    const job = (state.data.agent_jobs || []).find((item) => item.id === jobId);
    if (job && ["draft_ready", "failed"].includes(job.status)) {
      clearInterval(state.pollTimer);
      if (job.status === "draft_ready") {
        banner("Agent 草稿已生成，请打开审查。 ");
        openReview(jobId);
      } else banner(job.error || "Agent 生成失败。", true);
    }
  }, 3000);
}

async function openReview(jobId) {
  try {
    const job = await api(`/api/agent/jobs/${jobId}`);
    state.currentJob = job;
    state.approvalToken = null;
    $("#review-title").textContent = job.assignment_name;
    $("#review-status").textContent = `${jobStatusText(job.status)} · ${job.course_code || job.course_name} · ${fmtDate(job.due_at)}`;
    $("#review-draft").value = job.draft || "";
    $("#review-draft").disabled = !["draft_ready", "approved"].includes(job.status);
    const artifacts = job.artifacts || [];
    $("#artifact-list").innerHTML = artifacts.length ? artifacts.map((item) => `<label class="artifact-row"><input type="checkbox" data-artifact-path="${esc(item.path)}" ${item.name === "agent-last-message.md" ? "" : "checked"}><span>${esc(item.name)} · ${Math.ceil((item.size || 0) / 1024)} KB</span><a href="/api/agent/jobs/${job.id}/artifacts/${esc(encodeURI(item.path))}">下载</a></label>`).join("") : '<p class="muted-note">没有文件产物，将使用文本草稿。</p>';
    const allowed = (job.submission_types || []).filter((item) => ["online_text_entry", "online_upload"].includes(item));
    $("#submission-type").innerHTML = allowed.map((item) => `<option value="${item}">${item === "online_upload" ? "文件上传" : "文本输入"}</option>`).join("");
    $("#reviewed-check").checked = false;
    $("#approval-confirmation").value = "";
    $("#review-notes").value = job.review_notes || "";
    $("#approval-message").textContent = "";
    $("#approve-button").disabled = Boolean(job.test_mode);
    $("#approve-button").textContent = job.test_mode ? "测试模式不可批准" : "保存审查批准";
    $("#final-submit-button").disabled = true;
    $("#final-submit-button").textContent = state.data.agent.submission_enabled ? "最终提交" : "最终提交（锁定）";
    if (job.test_mode) setApprovalMessage("这是已完成作业的测试草稿：可以编辑和下载 PDF，但永久不能批准或提交。", false);
    showReview(true);
  } catch (err) { banner(err.message, true); }
}

async function saveReviewedDraft() {
  if (!state.currentJob) return;
  try {
    await api(`/api/agent/jobs/${state.currentJob.id}/draft`, {
      method: "PATCH", body: JSON.stringify({ content: $("#review-draft").value }),
    });
    state.approvalToken = null;
    $("#final-submit-button").disabled = true;
    banner("草稿修改已保存，之前的批准（如有）已撤销。");
    await openReview(state.currentJob.id);
    await loadDashboard();
  } catch (err) { setApprovalMessage(err.message, true); }
}

async function approveCurrentJob() {
  if (!state.currentJob) return;
  const artifactPaths = $$('[data-artifact-path]:checked').map((item) => item.dataset.artifactPath);
  try {
    const result = await api(`/api/agent/jobs/${state.currentJob.id}/approve`, {
      method: "POST",
      body: JSON.stringify({
        reviewed: $("#reviewed-check").checked,
        confirmation: $("#approval-confirmation").value,
        submission_type: $("#submission-type").value,
        artifact_paths: artifactPaths,
        review_notes: $("#review-notes").value,
      }),
    });
    state.approvalToken = result.approval_token;
    const enabled = state.data.agent.submission_enabled;
    $("#final-submit-button").disabled = !enabled;
    setApprovalMessage(enabled ? "审查批准已保存。十分钟内可执行最终提交。" : "审查批准已保存，但全局提交开关关闭，本次无法上传。", false);
    await loadDashboard();
  } catch (err) { setApprovalMessage(err.message, true); }
}

async function submitCurrentJob() {
  if (!state.currentJob || !state.approvalToken) return;
  const message = `即将向 eLearning 提交“${state.currentJob.assignment_name}”。这是不可撤销的外部操作，确认继续吗？`;
  if (!window.confirm(message)) return;
  try {
    await api(`/api/agent/jobs/${state.currentJob.id}/submit`, {
      method: "POST",
      body: JSON.stringify({ confirm_submit: true, approval_token: state.approvalToken }),
    });
    banner("eLearning 已返回提交成功。 ");
    showReview(false);
    await loadDashboard();
  } catch (err) { setApprovalMessage(err.message, true); }
}

function setApprovalMessage(message, error) {
  const element = $("#approval-message");
  element.textContent = message;
  element.className = `approval-message${error ? " error" : ""}`;
}

init();
