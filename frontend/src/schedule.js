// Current-user OS schedules, with a separate preview before registration.
import { dsWithCode, tr } from "./i18n.js";
export async function renderSchedule(ctx, home, alive) {
  const { api, esc } = ctx;
  const host = document.getElementById("ops-schedule");
  if (!host) return;
  const revision = (host.scheduleRevision || 0) + 1;
  host.scheduleRevision = revision;
  const pageAlive = alive;
  alive = () => pageAlive() && host.scheduleRevision === revision;
  let value;
  try {
    value = await api("/api/ops/schedule");
  } catch (err) {
    if (alive()) host.innerHTML = `<p class="panel-note err">${tr("定时任务", "Schedule")}: ${esc(err.message)}</p>`;
    return;
  }
  if (!alive()) return;
  const native = value.native || {};
  const writable = home.mode.ops_enabled;
  const disabled = writable ? "" : "disabled";
  const datasets = (value.backup_choices || []).map((name) => `<option value="${esc(name)}" ${(value.backup_datasets || []).includes(name) ? "selected" : ""}>${esc(dsWithCode(name))}</option>`).join("");
  const eventGroups = (value.events_groups || []).map((name) => `<option value="${esc(name)}" ${value.events_group === name ? "selected" : ""}>${esc(name)}</option>`).join("");
  const last = value.last_tick;
  const links = (last?.jobs || []).map((job) => `<a href="#/ops/jobs/${encodeURIComponent(job.job_id)}">${esc(job.job)} · ${esc(job.session)}</a>`).join(" · ");
  const status = native.error ? tr("系统状态未知", "System status unknown") : value.enabled ? tr("已启用", "Enabled") : tr("已暂停 / 未启用", "Paused / not enabled");
  const tickLabels = { idle: "已检查，当前无到期任务", started: "已启动任务", waiting: "等待占用结束", error: "调度出错", busy: "另一检查正在运行" };
  host.innerHTML = `<div class="panel-header"><h2>${tr("定时任务", "Schedule")}</h2><span class="status-pill">${esc(status)}</span></div>
    <p class="panel-note">${esc(value.backend)} · ${esc(value.note)}</p>
    ${native.error ? `<p class="panel-note err">${esc(native.error)}</p>` : ""}
    ${value.warning ? `<p class="panel-note err">${esc(value.warning)}</p>` : ""}
    ${value.timer_health === "stale" ? `<p class="panel-note err">超过 20 分钟未收到系统触发，请检查登录状态、休眠或 cron 服务，再刷新状态。</p>` : ""}
    ${value.timer_health === "not_seen" ? `<p class="panel-note">已注册，等待首次系统检查（最多约 1 分钟）。</p>` : ""}
    ${native.installed && (!native.active || !native.matches) ? `<p class="panel-note err">系统任务未加载或定义与当前安装不符，请核对后重新设置。</p>` : ""}
    ${last ? `<p class="panel-note">最近检查：${esc(last.at)} · ${esc(tickLabels[last.status] || last.status)} ${esc(last.message || "")} ${links}</p>` : `<p class="panel-note">尚无调度检查记录。</p>`}
    <form id="schedule-fields" class="ops-fields">
      <label class="ops-check"><input id="schedule-daily" type="checkbox" ${value.daily ? "checked" : ""} ${writable ? "" : "disabled"}> 自动日更（未选研究包时跑全部日更组与事件流）</label>
      <fieldset class="ops-fields">
        <legend>研究包（都不选则保持全部日更）</legend>
        <label class="ops-check"><input id="schedule-pack-market" type="checkbox" ${(value.daily_packs || []).includes("market") ? "checked" : ""} ${writable ? "" : "disabled"}> 行情</label>
        <label class="ops-check"><input id="schedule-pack-fundamentals" type="checkbox" ${(value.daily_packs || []).includes("fundamentals") ? "checked" : ""} ${writable ? "" : "disabled"}> 基本面</label>
        <label class="ops-check"><input id="schedule-pack-universe" type="checkbox" ${(value.daily_packs || []).includes("universe") ? "checked" : ""} ${writable ? "" : "disabled"}> 股票池（无额外日更组）</label>
      </fieldset>
      <label>日更时间（北京时间）<input id="schedule-daily-at" type="time" required value="${esc(value.daily_run_at)}" ${writable ? "" : "disabled"}></label>
      <label class="ops-check"><input id="schedule-stale" type="checkbox" ${value.stale ? "checked" : ""} ${writable ? "" : "disabled"}> 收尾补抓（落后的快照）</label>
      <label>补抓时间（北京时间）<input id="schedule-stale-at" type="time" required value="${esc(value.stale_run_at)}" ${writable ? "" : "disabled"}></label>
      <label class="ops-check"><input id="schedule-backup" type="checkbox" ${value.backup ? "checked" : ""} ${disabled}> 每日数据备份（自然日）</label>
      <label>备份时间（北京时间）<input id="schedule-backup-at" type="time" required value="${esc(value.backup_run_at || "23:00")}" ${disabled}></label>
      <label>备份数据集<select id="schedule-backup-datasets" multiple size="4" ${disabled}>${datasets}</select><small class="muted">只复制所选已发布数据及对应元数据，不包含配置、凭据或完整运行数据库。</small></label>
      <label>备份目录（绝对路径）<input id="schedule-backup-root" value="${esc(value.backup_root)}" ${disabled}><small class="muted">建议选择外部磁盘。备份占用空间，不自动删除旧备份。</small></label>
      <label class="ops-check"><input id="schedule-events" type="checkbox" ${value.events ? "checked" : ""} ${disabled}> 独立事件流（含周末和节假日）</label>
      <label>事件组<select id="schedule-events-group" ${disabled}><option value="">请选择</option>${eventGroups}</select></label>
      <label>事件流间隔（分钟）<input id="schedule-events-interval" type="number" min="1" max="1440" required value="${esc(value.events_interval_minutes || 60)}" ${disabled}><small class="muted">按上次尝试开始时间计算间隔，任务占用时等待下一次检查。</small></label>
      <div class="action-row"><button class="button button-primary" type="submit" ${writable ? "" : "disabled"}>预览定时设置</button><button class="button button-ghost" id="schedule-refresh" type="button">刷新状态</button></div>
    </form><div id="schedule-preview"></div>`;
  const form = document.getElementById("schedule-fields");
  const target = document.getElementById("schedule-preview");
  let previewGeneration = 0;
  form.oninput = () => {
    previewGeneration += 1;
    target.innerHTML = "";
  };
  document.getElementById("schedule-refresh").onclick = () => renderSchedule(ctx, home, pageAlive);
  form.onsubmit = async (event) => {
    event.preventDefault();
    const current = ++previewGeneration;
    target.innerHTML = `<p class="panel-note">正在预览…</p>`;
    const settings = {
      daily: document.getElementById("schedule-daily").checked,
      stale: document.getElementById("schedule-stale").checked,
      daily_run_at: document.getElementById("schedule-daily-at").value,
      stale_run_at: document.getElementById("schedule-stale-at").value,
      backup: document.getElementById("schedule-backup").checked,
      backup_run_at: document.getElementById("schedule-backup-at").value || value.backup_run_at || "23:00",
      backup_datasets: [...(document.getElementById("schedule-backup-datasets").selectedOptions || [])].map((option) => option.value),
      backup_root: document.getElementById("schedule-backup-root").value.trim() || null,
      events: document.getElementById("schedule-events").checked,
      events_group: document.getElementById("schedule-events-group").value || null,
      events_interval_minutes: Number(document.getElementById("schedule-events-interval").value || value.events_interval_minutes || 60),
      daily_packs: ["market", "fundamentals", "universe"].filter((name) => document.getElementById(`schedule-pack-${name}`).checked),
    };
    try {
      const preview = await api("/api/ops/schedule/preview", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CNE-CSRF": home.csrf_token },
        body: JSON.stringify(settings),
      });
      if (!alive() || current !== previewGeneration) return;
      target.innerHTML = `<p class="panel-note">${esc(preview.action)} · ${esc(preview.backend)}（时间均为北京时间）</p>
        <ul class="ops-list"><li>日更：${preview.daily ? esc(preview.daily_run_at) : "关闭"}${preview.daily_packs?.length ? ` · 研究包 ${esc(preview.daily_packs.join("、"))}` : ""}；补抓：${preview.stale ? esc(preview.stale_run_at) : "关闭"}</li>
        <li>${tr("每日备份", "Daily backup")}: ${preview.backup ? `${esc(preview.backup_run_at)} · ${esc((preview.backup_datasets || []).map(dsWithCode).join(", "))} → ${esc(preview.backup_root)}` : tr("关闭", "off")}</li>
        <li>独立事件流：${preview.events ? `${esc(preview.events_group)} · 每 ${esc(preview.events_interval_minutes)} 分钟` : "关闭"}</li></ul>
        <details><summary>查看系统任务定义</summary><pre class="ops-log">${esc(preview.artifact || "移除本页面创建的定时触发。")}</pre></details>
        <label class="ops-check"><input id="schedule-ack" type="checkbox"> ${esc(preview.confirmation)}</label>
        <div class="action-row"><button class="button button-primary" id="schedule-apply" type="button" disabled>确认${esc(preview.action)}</button></div>
        <p class="panel-note" id="schedule-note"></p>`;
      const button = document.getElementById("schedule-apply");
      document.getElementById("schedule-ack").onchange = (event) => { button.disabled = !event.target.checked; };
      button.onclick = async () => {
        if (!alive() || current !== previewGeneration) return;
        button.disabled = true;
        try {
          await api("/api/ops/schedule/apply", {
            method: "POST", headers: { "Content-Type": "application/json", "X-CNE-CSRF": home.csrf_token },
            body: JSON.stringify({ token: preview.token, acknowledged: true }),
          });
          if (alive()) await renderSchedule(ctx, home, pageAlive);
        } catch (err) {
          if (alive()) document.getElementById("schedule-note").textContent = `${err.message} 请刷新状态并重新预览。`;
        }
      };
    } catch (err) {
      if (alive() && current === previewGeneration) target.innerHTML = `<p class="panel-note err">${esc(err.message)}</p>`;
    }
  };
}
