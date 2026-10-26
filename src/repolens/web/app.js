const state = { taskId: null, report: null, polling: null };

const elements = {
  form: document.querySelector("#analysis-form"),
  url: document.querySelector("#repository-url"),
  target: document.querySelector("#analysis-target"),
  count: document.querySelector("#target-count"),
  enableModel: document.querySelector("#enable-model"),
  submit: document.querySelector("#submit-button"),
  progressPanel: document.querySelector("#progress-panel"),
  progressValue: document.querySelector("#progress-value"),
  progressBar: document.querySelector("#progress-bar"),
  progressStage: document.querySelector("#progress-stage"),
  errorPanel: document.querySelector("#error-panel"),
  errorMessage: document.querySelector("#error-message"),
  reportPanel: document.querySelector("#report-panel"),
  confirmationMessage: document.querySelector("#confirmation-message"),
};

const labels = {
  maintenance: "维护活跃度",
  engineering: "工程质量",
  license: "许可证风险",
  target_fit: "目标适配",
  fact: "事实",
  inference: "推断",
  needs_confirmation: "待确认",
  recommended: "推荐采用",
  cautious: "谨慎采用",
  not_recommended: "不推荐",
  rule_only: "规则模式",
  hybrid: "规则 + Agent",
};

elements.target.addEventListener("input", () => {
  elements.count.textContent = `${elements.target.value.length} / 2000`;
});

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetView();
  setSubmitting(true);
  try {
    const task = await api("/api/analyses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repository_url: elements.url.value.trim(),
        target: elements.target.value.trim(),
        enable_model: elements.enableModel.checked,
      }),
    });
    state.taskId = task.task_id;
    elements.progressPanel.classList.remove("hidden");
    updateProgress(task);
    await pollTask();
  } catch (error) {
    showError(error.message);
    setSubmitting(false);
  }
});

document.querySelector("#retry-button").addEventListener("click", () => {
  elements.errorPanel.classList.add("hidden");
  elements.form.requestSubmit();
});
document.querySelector("#export-json").addEventListener("click", () => {
  if (state.report) download("repolens-report.json", JSON.stringify(state.report, null, 2), "application/json");
});
document.querySelector("#export-markdown").addEventListener("click", () => {
  if (state.report) download("repolens-report.md", reportToMarkdown(state.report), "text/markdown");
});
document.querySelector("#confirm-button").addEventListener("click", confirmReport);

async function pollTask() {
  clearTimeout(state.polling);
  try {
    const task = await api(`/api/analyses/${state.taskId}`);
    updateProgress(task);
    if (task.status === "failed") {
      showError(task.error?.message || "分析任务失败");
      setSubmitting(false);
      return;
    }
    if (["awaiting_confirmation", "completed"].includes(task.status)) {
      state.report = await api(`/api/analyses/${state.taskId}/report`);
      renderReport(state.report);
      setSubmitting(false);
      return;
    }
    state.polling = setTimeout(pollTask, 900);
  } catch (error) {
    showError(error.message);
    setSubmitting(false);
  }
}

function updateProgress(task) {
  const progress = Math.max(0, Math.min(100, task.progress || 0));
  elements.progressValue.textContent = `${progress}%`;
  elements.progressBar.style.width = `${progress}%`;
  elements.progressStage.textContent = task.current_stage || "任务正在排队";
  const order = ["collecting", "reviewing", "verifying", "awaiting_confirmation"];
  const current = order.indexOf(task.status);
  document.querySelectorAll(".pipeline li").forEach((item, index) => {
    item.classList.toggle("active", index === current);
    item.classList.toggle("done", index < current || task.status === "completed");
  });
}

function renderReport(report) {
  elements.progressPanel.classList.add("hidden");
  elements.reportPanel.classList.remove("hidden");
  document.querySelector("#report-repository").textContent = report.repository.full_name;
  document.querySelector("#report-summary").textContent = report.executive_summary;
  document.querySelector("#overall-score").textContent = report.overall_score;
  const recommendation = document.querySelector("#recommendation-badge");
  recommendation.textContent = labels[report.recommendation] || report.recommendation;
  recommendation.className = `recommendation ${report.recommendation.replaceAll("_", "-")}`;
  document.querySelector("#analysis-mode").textContent = labels[report.analysis_mode] || report.analysis_mode;
  document.querySelector("#section-grid").innerHTML = report.sections.map(sectionCard).join("");
  document.querySelector("#risk-list").innerHTML = report.risks.length
    ? report.risks.map(riskCard).join("")
    : '<div class="empty-state">没有进入已验证列表的中高风险项</div>';
  renderConfirmations(report);
  const confirmButton = document.querySelector("#confirm-button");
  const targetConfirmed = document.querySelector("#target-confirmed");
  if (report.status === "confirmed") {
    confirmButton.disabled = true;
    targetConfirmed.checked = true;
    targetConfirmed.disabled = true;
    document.querySelectorAll(".risk-check").forEach(item => { item.checked = true; item.disabled = true; });
    elements.confirmationMessage.textContent = "报告已经人工确认并完成。";
  } else {
    confirmButton.disabled = false;
    targetConfirmed.checked = false;
    targetConfirmed.disabled = false;
    document.querySelector("#confirmation-notes").value = "";
    elements.confirmationMessage.textContent = "";
  }
  elements.reportPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function sectionCard(section) {
  const visibleFindings = section.findings.slice(0, 6);
  return `<article class="review-card">
    <header><h3>${escapeHtml(labels[section.kind] || section.kind)}</h3><span class="section-score">${section.score}</span></header>
    <p>${escapeHtml(section.summary)}</p>
    <div class="finding-list">${visibleFindings.map(findingCard).join("")}</div>
  </article>`;
}

function findingCard(finding) {
  const tagClass = finding.claim_type.replaceAll("_", "-");
  return `<div class="finding">
    <div class="finding-row"><strong>${escapeHtml(finding.title)}</strong><span class="finding-tag ${tagClass}">${escapeHtml(labels[finding.claim_type] || finding.claim_type)}</span></div>
    <p>${escapeHtml(finding.description)}</p>
  </div>`;
}

function riskCard(risk) {
  const evidence = risk.evidence.length
    ? `<details><summary>查看 ${risk.evidence.length} 条证据</summary><ul class="evidence-list">${risk.evidence.map(item => `<li><a href="${escapeAttribute(item.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.field_or_path)}</a> · ${escapeHtml(item.kind)}</li>`).join("")}</ul></details>`
    : "";
  return `<article class="risk-item ${risk.severity}">
    <header><h3>${escapeHtml(risk.title)}</h3><span class="finding-tag ${risk.claim_type.replaceAll("_", "-")}">${escapeHtml(risk.severity)}</span></header>
    <p>${escapeHtml(risk.description)}</p>${evidence}
  </article>`;
}

function renderConfirmations(report) {
  const risks = report.risks.filter(item => ["high", "critical"].includes(item.severity));
  document.querySelector("#risk-confirmations").innerHTML = risks.length
    ? risks.map(risk => `<label class="check-row"><input class="risk-check" type="checkbox" value="${escapeAttribute(risk.finding_id)}"><span>我已了解：${escapeHtml(risk.title)}</span></label>`).join("")
    : '<div class="empty-state">没有必须逐条接受的高风险项</div>';
}

async function confirmReport() {
  elements.confirmationMessage.textContent = "";
  const targetConfirmed = document.querySelector("#target-confirmed").checked;
  const acceptedRiskIds = [...document.querySelectorAll(".risk-check:checked")].map(item => item.value);
  try {
    const report = await api(`/api/analyses/${state.taskId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_confirmed: targetConfirmed,
        accepted_risk_ids: acceptedRiskIds,
        notes: document.querySelector("#confirmation-notes").value.trim() || null,
      }),
    });
    state.report = report;
    renderReport(report);
  } catch (error) {
    elements.confirmationMessage.textContent = error.message;
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const suffix = body.request_id ? `（请求 ${body.request_id}）` : "";
    throw new Error(`${body.message || "请求失败"}${suffix}`);
  }
  return body;
}

function resetView() {
  clearTimeout(state.polling);
  state.taskId = null;
  state.report = null;
  elements.errorPanel.classList.add("hidden");
  elements.reportPanel.classList.add("hidden");
  elements.progressPanel.classList.add("hidden");
}

function setSubmitting(value) {
  elements.submit.disabled = value;
  elements.submit.querySelector("span").textContent = value ? "分析进行中" : "开始尽调";
}

function showError(message) {
  clearTimeout(state.polling);
  elements.progressPanel.classList.add("hidden");
  elements.errorMessage.textContent = message;
  elements.errorPanel.classList.remove("hidden");
}

function reportToMarkdown(report) {
  const sections = report.sections.map(section => `## ${labels[section.kind] || section.kind} · ${section.score}/100\n\n${section.summary}\n\n${section.findings.map(item => `- **${item.title}**：${item.description}`).join("\n")}`).join("\n\n");
  const risks = report.risks.map(item => `- **[${item.severity}] ${item.title}**：${item.description}`).join("\n") || "- 无已验证的中高风险";
  return `# ${report.repository.full_name} 技术尽调报告\n\n> ${report.executive_summary}\n\n- 综合评分：${report.overall_score}/100\n- 建议：${labels[report.recommendation] || report.recommendation}\n- 目标：${report.target}\n\n${sections}\n\n## 风险\n\n${risks}\n\n## 一周建议\n\n${report.development_plan.map(item => `- ${item}`).join("\n")}\n`;
}

function download(filename, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function escapeAttribute(value) { return escapeHtml(value); }
