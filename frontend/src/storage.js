// A review is a proposal. Only a separate, explicit confirmation executes it.
const labels = { due: "已到期，待确认", observing: "观察期内", unmarked: "尚未标记", protected: "保留" };
const reasons = {
  current_generation: "当前版本", recent_generation: "最近 5 代", explicit_hold: "审计或人工保留",
  referenced_path: "仍有路径引用", active_experiment: "试验仍在使用", archive_required: "缺少独立归档",
  source_missing: "原目录不存在", missing_current_pointer: "缺少当前版本指针",
  unreceipted_generation: "缺少版本收据", missing_file_manifest: "缺少文件清单",
};
const size = n => n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(2)} GiB` : n >= 2 ** 20 ? `${(n / 2 ** 20).toFixed(2)} MiB` : `${(n / 1024).toFixed(1)} KiB`;
const date = s => s ? new Date(s).toLocaleString("zh-CN", { hour12: false }) : "—";
let generation = 0;
export function closeStorage() { generation++; }

export async function renderStorage({ api, setPage, esc, dataTable }) {
  const current = ++generation;
  let summary, review, running = false;
  const alive = () => current === generation;
  const post = (path, body) => api(path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-CNE-Storage-CSRF": summary.csrf_token },
    body: JSON.stringify(body),
  });
  const rows = objects => objects.map(o => `<tr><td>${esc(o.label)}<small class="storage-id">${esc(o.object_id)}</small></td><td class="n">${size(o.logical_bytes)}</td></tr>`);
  function showMessage(text, error = false) {
    const target = document.getElementById("storage-message");
    if (target && alive()) { target.className = `panel-note${error ? " err" : ""}`; target.textContent = text; }
  }
  function controls(disabled) {
    running = disabled;
    document.querySelectorAll("[data-storage-action], #storage-kind").forEach(el => el.disabled = disabled);
  }
  async function poll(job) {
    while (alive() && ["checking", "executing"].includes(job.status)) {
      await new Promise(resolve => setTimeout(resolve, 800));
      if (!alive()) return null;
      job = await api(`/api/storage/jobs/${job.job_id}`);
    }
    if (!alive()) return null;
    if (job.status === "error") throw new Error(`${job.error} ${job.message || ""}`);
    return job;
  }
  function showReview(job) {
    review = job;
    const purge = job.phase === "purge";
    const target = document.getElementById("storage-review");
    if (!job.objects.length) {
      target.innerHTML = "";
      showMessage(purge ? "本次检查没有可删除的项目。未到期、被引用或受保护的数据会继续保留。" : "没有可标记的项目。");
      return;
    }
    showMessage("检查完成。请核对下方清单；确认在 10 分钟内有效，执行时会再次核验保护条件。");
    target.innerHTML = `<section class="surface-panel report-panel storage-confirm" aria-labelledby="storage-review-title">
      <div class="panel-header"><div><div class="eyebrow">${purge ? "等待删除确认" : "开始观察期"}</div><h2 id="storage-review-title" tabindex="-1">${purge ? "确认永久删除" : "确认标记"} ${job.objects.length} 项</h2></div><span class="panel-meta">账面大小 ${size(job.logical_bytes)}</span></div>
      <p class="panel-note">${purge ? "只处理以下清单。删除后无法从原路径读取这些历史数据；当前版本及受保护对象继续保留。APFS 克隆的账面大小不等于实际释放空间。" : "只记录待删除状态，至少观察 7 天，不释放空间；未变化的已有标记保留原观察起点。到期后仍须再次检查并在网页确认删除。"}${job.resuming ? " 此清单包含上次操作已完成的项目，恢复执行会跳过它们。" : ""}</p>
      ${dataTable(["本次范围", { h: "账面大小", n: true }], rows(job.objects), "没有项目")}
      <div class="storage-consent"><label><input type="checkbox" id="storage-ack">${purge ? "我已核对清单，理解删除不可撤销，确认删除这些历史数据。" : "我已核对清单，确认开始观察期。"}</label>
      ${purge ? '<label><input type="checkbox" id="storage-idle">我已停止外部查询、其他 serve 实例、采集调度和试验写入，确保维护期间不会重新启动。</label>' : ""}
      <div class="action-row"><button class="button ${purge ? "button-danger" : "button-primary"}" id="storage-confirm" disabled>${purge ? "永久删除这" : "标记这"} ${job.objects.length} 项</button><button class="button button-ghost" id="storage-cancel">取消</button></div></div></section>`;
    const button = document.getElementById("storage-confirm");
    const consent = () => { button.disabled = !document.getElementById("storage-ack").checked || (purge && !document.getElementById("storage-idle").checked); };
    target.querySelectorAll("input").forEach(el => el.addEventListener("change", consent));
    document.getElementById("storage-cancel").onclick = () => { review = null; target.innerHTML = ""; showMessage("已取消，没有执行此清单。"); };
    button.onclick = async () => {
      if (running || !review) return;
      controls(true);
      target.querySelectorAll("button, input").forEach(el => el.disabled = true);
      showMessage(purge ? "正在执行已确认的清单。本面板读取暂时暂停，请保持外部任务停止。" : "正在记录观察期…");
      try {
        const job = await post("/api/storage/confirm", { review_id: review.job_id, confirmation_token: review.confirmation_token, confirmed: true, maintenance_confirmed: purge });
        await finish(job);
      } catch (err) { if (alive()) { target.innerHTML = ""; review = null; await reloadAfterError(err); } }
      finally { if (alive()) controls(false); }
    };
    document.getElementById("storage-review-title").focus();
  }
  async function finish(job) {
    const result = await poll(job);
    if (!result) return;
    const purge = result.phase === "purge";
    summary = await api("/api/storage");
    if (!alive()) return;
    paint();
    showMessage(purge ? "已完成确认范围内的删除。下方磁盘可用空间已刷新；其变化也可能受其他程序和系统快照影响。" : "已标记并开始观察期。至少 7 天后重新检查，删除仍需在此页面确认。");
    document.getElementById("storage-review").innerHTML = `<details class="surface-panel storage-result"><summary>查看执行记录</summary><pre>${esc(JSON.stringify(result.result, null, 2))}</pre></details>`;
  }
  async function reloadAfterError(err) {
    try { summary = await api("/api/storage"); if (alive()) paint(); } catch { /* retain the original error */ }
    showMessage(err.message, true);
  }
  async function check(phase, resume) {
    if (running) return;
    controls(true);
    review = null;
    document.getElementById("storage-review").innerHTML = "";
    showMessage("正在核验引用、版本和文件完整性。大目录检查可能需要几分钟；这一步不会删除数据。");
    try {
      const kind = resume?.kind || document.getElementById("storage-kind").value;
      const job = await poll(await post("/api/storage/reviews", { kind, phase, ...(resume ? { resume_plan_id: resume.plan_id } : {}) }));
      if (job) showReview(job);
    } catch (err) { showMessage(err.message, true); }
    finally { if (alive()) controls(false); }
  }
  function paint() {
    review = null;
    const counts = Object.fromEntries(Object.keys(labels).map(s => [s, summary.objects.filter(o => o.status === s)]));
    const countText = s => `${counts[s].length} 项 · ${size(counts[s].reduce((n, o) => n + o.logical_bytes, 0))}`;
    setPage(`<section class="page-heading"><div class="eyebrow">数据湖控制台 / 存储运维</div><div class="heading-row"><div><h1>存储运维</h1><p class="sub">历史版本与试验目录到期后只提示，只有在此确认才会删除。</p></div><span class="status-pill status-pill-fresh">自动删除已关闭</span></div></section>
      <section class="surface-panel report-panel"><div class="panel-header"><div><h2>清理提示</h2><p class="sub">磁盘可用 ${size(summary.disk_free_bytes)} / ${size(summary.disk_total_bytes)}</p></div></div>
      <dl class="storage-stats">${["due", "observing", "unmarked", "protected"].map(s => `<div><dt>${labels[s]}</dt><dd>${countText(s)}</dd></div>`).join("")}</dl>
      <p class="panel-note">每个数据集保留最近 5 代及当前版本；引用和人工保留优先。标记后至少观察 7 天，达到期限不代表一定可删。大小均为账面值，不能视为实际可释放空间。</p>
      ${!summary.registered ? '<p class="panel-note err">尚未导入生命周期引用清单。请先通过 storage import 完成登记，检查会在缺少保护依据时停止。</p>' : ""}
      <div class="storage-actions"><label for="storage-kind">检查范围</label><select id="storage-kind"><option value="revisions">历史版本</option><option value="experiments">试验目录</option></select><button class="button button-primary" data-storage-action id="storage-check">检查到期项目</button><button class="button button-ghost" data-storage-action id="storage-mark">检查待标记项目</button></div>
      <p id="storage-message" class="panel-note" role="status" aria-live="polite">先检查生成清单，再核对并确认。浏览或刷新此页不会删除数据。</p></section>
      <div id="storage-review" class="storage-review"></div>
      ${summary.unfinished.length ? `<section class="surface-panel report-panel storage-review"><h2>有未完成的删除记录</h2><p class="panel-note">先重新审核原清单。已完成的项目不会重复删除，剩余项目仍需确认。</p>${summary.unfinished.map((x, i) => `<div class="storage-resume"><code>${esc(x.plan_id)}</code><button class="button button-ghost" data-storage-action data-resume="${i}">重新审核${x.kind === "revisions" ? "版本" : "试验"}清单</button></div>`).join("")}</section>` : ""}
      <section class="surface-panel report-panel storage-review"><div class="panel-header"><h2>对象与保留原因</h2><label>显示 <select id="storage-filter"><option value="pending">待处理</option><option value="protected">保留</option><option value="all">全部</option></select></label></div><div id="storage-objects"></div></section>`, "storage");
    function filter() {
      const value = document.getElementById("storage-filter").value;
      const objects = summary.objects.filter(o => value === "all" || (value === "protected" ? o.status === "protected" : o.status !== "protected"));
      document.getElementById("storage-objects").innerHTML = dataTable(["对象", "状态与原因", "最早可审核时间（本地）", { h: "账面大小", n: true }], objects.map(o => `<tr><td>${esc(o.label)}<small class="storage-id">${o.kind === "revisions" ? "历史版本" : "试验目录"}</small></td><td>${labels[o.status]}${o.reasons.length ? `<small class="storage-id">${o.reasons.map(r => esc(reasons[r] || r)).join("、")}</small>` : ""}</td><td>${esc(date(o.not_before))}</td><td class="n">${size(o.logical_bytes)}</td></tr>`), "此分类下没有项目。");
    }
    document.getElementById("storage-filter").onchange = filter;
    filter();
    document.getElementById("storage-check").onclick = () => check("purge");
    document.getElementById("storage-mark").onclick = () => check("mark");
    document.querySelectorAll("[data-resume]").forEach(el => el.onclick = () => check("purge", summary.unfinished[Number(el.dataset.resume)]));
  }
  setPage('<section class="page-heading"><h1>存储运维</h1><p class="sub" role="status">正在读取存储状态…</p></section>', "storage");
  summary = await api("/api/storage");
  if (!alive()) return;
  paint();
  const active = summary.active_jobs[0];
  if (active) {
    controls(true);
    showMessage(active.status === "executing" ? "已有用户确认的操作正在执行，正在读取进度…" : "正在等待已有检查完成…");
    try { if (active.status === "executing") await finish(active); else { const job = await poll(active); if (job) showReview(job); } }
    catch (err) { await reloadAfterError(err); }
    finally { if (alive()) controls(false); }
  }
}
