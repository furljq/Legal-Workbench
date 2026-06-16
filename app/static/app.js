const state = {
  capabilities: [],
  activeCapabilityId: null,
  progressTimer: null,
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setBadge(text) {
  $("#stateBadge").textContent = text;
}

function setModelBadge(text, status = "") {
  const badge = $("#modelBadge");
  badge.textContent = text;
  badge.className = ["badge", "model-badge", status].filter(Boolean).join(" ");
}

function labelForStatus(value) {
  const labels = {
    shell: "工作台搭建中",
    placeholder: "工作台已连通",
    parsed: "文件已解析",
    partial_error: "部分文件未解析",
  };
  return labels[value] || value || "未设置";
}

function labelForPhase(value) {
  const labels = {
    "workbench-check": "工作台连通性测试",
    "v0.3-docx-intake": "交易文件解析",
    "v0.4-source-index": "原文证据索引",
    "v0.4-kts-candidates": "KTS 候选证据",
    "v0.4-kts-extraction": "KTS 摘要生成",
  };
  return labels[value] || value || "未设置";
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function renderCapabilities() {
  const nav = $("#capabilityNav");
  nav.innerHTML = "";
  $("#capabilityCount").textContent = `${state.capabilities.length} 项`;

  for (const capability of state.capabilities) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "capability-tab";
    if (capability.capability_id === state.activeCapabilityId) {
      button.classList.add("active");
    }
    button.innerHTML = `
      <strong>${capability.title}</strong>
      <span>${capability.stage_label || labelForStatus(capability.status)}</span>
    `;
    button.addEventListener("click", () => selectCapability(capability.capability_id));
    nav.appendChild(button);
  }
}

async function selectCapability(capabilityId) {
  state.activeCapabilityId = capabilityId;
  renderCapabilities();
  const capability = await fetchJson(`/api/capabilities/${capabilityId}`);
  $("#capabilityTitle").textContent = capability.title;
  $("#capabilityDescription").textContent = capability.description;
  $("#capabilityJson").textContent = JSON.stringify(capability, null, 2);
}

function renderRunSummary(result) {
  const current = result.current || {};
  const currentResult = current.result || {};
  const capability = currentResult.capability || {};
  if (Array.isArray(currentResult.documents)) {
    return renderDocumentSummary(result);
  }
  return `
    <div class="status-line">
      <strong>${escapeHtml(labelForStatus(currentResult.status) || "已连通")}</strong>
      <span>${escapeHtml(currentResult.message || "检查已完成。")}</span>
    </div>
    <dl>
      <div>
        <dt>能力</dt>
        <dd>${escapeHtml(capability.title || "融资交易 KTS")}</dd>
      </div>
      <div>
        <dt>阶段</dt>
        <dd>${escapeHtml(labelForPhase(current.phase))}</dd>
      </div>
      <div>
        <dt>检查时间</dt>
        <dd>${escapeHtml(current.updated_at || "未记录")}</dd>
      </div>
    </dl>
  `;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value >= 1024 * 1024) {
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }
  if (value >= 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${value} B`;
}

function createRunId() {
  const randomPart = Math.random().toString(36).slice(2, 10);
  return `run-${Date.now()}-${randomPart}`;
}

function renderProgressStages(progress) {
  const stageIndex = Number(progress.stage_index || 0);
  const done = progress.status === "completed";
  const stages = [
    { index: 1, label: "读取文件" },
    { index: 2, label: "证据索引" },
    { index: 3, label: "模型复核" },
    { index: 4, label: "生成摘要" },
  ];
  return stages
    .map((stage) => {
      let className = "pending";
      if (done || stage.index < stageIndex) {
        className = "completed";
      } else if (stage.index === stageIndex) {
        className = "active";
      }
      return `<span class="${className}">${escapeHtml(stage.label)}</span>`;
    })
    .join("");
}

function renderProcessingProgress(progress = {}) {
  const completedItems = Number(progress.completed_items || 0);
  const totalItems = Number(progress.total_items || 0);
  const ktsText = totalItems > 0 ? `KTS 事项 ${completedItems}/${totalItems}` : "KTS 事项待开始";
  return `
    <div class="status-line running">
      <strong>${escapeHtml(progress.stage_label || "正在处理交易文件")}</strong>
      <span>${escapeHtml(ktsText)}</span>
    </div>
    <div class="processing-steps">
      ${renderProgressStages(progress)}
    </div>
  `;
}

function startProcessingProgress(fileCount, runId) {
  const button = $("#uploadDocuments");
  let latestProgress = {
    progress_percent: 1,
    stage_label: "准备处理",
    message: `${fileCount} 个文件已提交。`,
  };
  let polling = false;
  button.disabled = true;
  button.textContent = "处理中";
  $("#resultBox").classList.remove("empty");
  const render = () => {
    $("#resultBox").innerHTML = renderProcessingProgress(latestProgress);
    setBadge("处理中");
  };
  const poll = async () => {
    if (polling) return;
    polling = true;
    try {
      const result = await fetchJson(`/api/runs/${encodeURIComponent(runId)}/progress`);
      latestProgress = result.progress || latestProgress;
    } catch (error) {
      latestProgress = {
        ...latestProgress,
        message: "正在等待后台进度更新。",
      };
    } finally {
      polling = false;
      render();
    }
  };
  render();
  poll();
  state.progressTimer = window.setInterval(poll, 1000);
  return () => {
    if (state.progressTimer) {
      window.clearInterval(state.progressTimer);
      state.progressTimer = null;
    }
    button.disabled = false;
    button.textContent = "生成候选证据";
  };
}

function renderEvidenceSummary(current) {
  const sourceSummary = current.source_index?.summary || null;
  const candidateSummary = current.kts_candidates?.summary || null;
  const extractionSummary = current.kts_extraction?.summary || null;
  if (!sourceSummary && !candidateSummary && !extractionSummary) {
    return "";
  }

  return `
    <div class="workflow-summary">
      <div>
        <strong>原文证据索引</strong>
        <span>${escapeHtml(sourceSummary?.raw_block_count ?? 0)} 个原文块，${escapeHtml(sourceSummary?.search_shard_count ?? 0)} 个检索切片</span>
      </div>
      <div>
        <strong>KTS 候选证据</strong>
        <span>${escapeHtml(candidateSummary?.candidate_item_count ?? 0)} 个事项找到候选，${escapeHtml(candidateSummary?.candidate_count ?? 0)} 条候选证据</span>
      </div>
      <div>
        <strong>模型语义复核</strong>
        <span>${renderModelReviewSummary(candidateSummary)}</span>
      </div>
      <div>
        <strong>KTS 摘要</strong>
        <span>${escapeHtml(extractionSummary?.draft_content_count ?? extractionSummary?.drafted_count ?? 0)} 个事项已形成摘要，${escapeHtml(extractionSummary?.needs_review_count ?? 0)} 个事项待复核，${escapeHtml(extractionSummary?.unclear_count ?? 0)} 个事项未明确</span>
      </div>
    </div>
  `;
}

function renderModelReviewSummary(candidateSummary) {
  const review = candidateSummary?.model_review || {};
  const reviewed = review.reviewed_item_count ?? candidateSummary?.ai_reviewed_item_count ?? 0;
  const scanned = review.scanned_item_count ?? candidateSummary?.ai_scanned_item_count ?? 0;
  const added = review.added_candidate_count ?? candidateSummary?.ai_added_candidate_count ?? 0;
  const errors = review.error_item_count ?? candidateSummary?.ai_error_count ?? 0;
  if (errors > 0) {
    return `${escapeHtml(reviewed)} 个事项已复核，${escapeHtml(scanned)} 个事项已补充扫描；${escapeHtml(errors)} 个事项待重新复核`;
  }
  return `${escapeHtml(reviewed)} 个事项已复核，${escapeHtml(scanned)} 个事项已补充扫描，新增 ${escapeHtml(added)} 条候选证据`;
}

function labelForExtractionStatus(value) {
  const labels = {
    drafted: "已形成摘要",
    needs_review: "待复核",
    unclear: "未明确",
  };
  return labels[value] || value || "未设置";
}

function labelForKtsGroup(value) {
  const labels = {
    SPA: "SPA",
    SHA: "SHA",
  };
  return labels[value] || value || "其他";
}

function renderReviewNotes(notes) {
  if (!Array.isArray(notes) || notes.length === 0) {
    return "";
  }
  return `
    <div class="kts-detail-block">
      <strong>复核要点</strong>
      <ul>
        ${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}
      </ul>
    </div>
  `;
}

function renderSourceEvidence(evidenceItems) {
  if (!Array.isArray(evidenceItems) || evidenceItems.length === 0) {
    return "";
  }
  return `
    <div class="kts-detail-block">
      <strong>来源证据</strong>
      <div class="evidence-list">
        ${evidenceItems
          .map((evidence, index) => {
            const verifiedLabel = evidence.verified ? "已核验" : "待核验";
            const verifiedClass = evidence.verified ? "ok" : "warn";
            return `
              <article class="evidence-item">
                <div class="evidence-head">
                  <span>证据 ${index + 1}</span>
                  <span class="pill ${verifiedClass}">${escapeHtml(verifiedLabel)}</span>
                </div>
                <p>${escapeHtml(evidence.quote || "未返回可展示原文。")}</p>
                <small>${escapeHtml(evidence.file_name || "未命名文件")}${evidence.source_locator ? ` · ${escapeHtml(evidence.source_locator)}` : ""}</small>
              </article>
            `;
          })
          .join("")}
      </div>
    </div>
  `;
}

function renderKtsDetail(item) {
  const notes = renderReviewNotes(item.review_notes);
  const evidence = renderSourceEvidence(item.source_evidence);
  if (!notes && !evidence) {
    return "";
  }
  return `
    <div class="kts-detail-row">
      <details class="kts-detail">
        <summary>查看证据与复核要点</summary>
        <div class="kts-detail-content">
          ${notes}
          ${evidence}
        </div>
      </details>
    </div>
  `;
}

function renderKtsDraftRows(items) {
  const rows = [];
  let currentGroup = "";
  for (const item of items) {
    const group = item.group || "OTHER";
    if (group !== currentGroup) {
      currentGroup = group;
      rows.push(`
        <div class="kts-row group">
          <strong>${escapeHtml(labelForKtsGroup(group))}</strong>
          <span></span>
          <span></span>
          <span></span>
        </div>
      `);
    }
    const note = Array.isArray(item.review_notes) ? item.review_notes[0] : "";
    rows.push(`
      <div class="kts-row">
        <strong>${escapeHtml(item.label || item.taxonomy_id || "未命名事项")}</strong>
        <span>${escapeHtml(labelForExtractionStatus(item.status))}</span>
        <p>${escapeHtml(item.draft_content || note || "待后续复核。")}</p>
        <span>${escapeHtml(item.source_evidence_count ?? 0)} 条</span>
      </div>
    `);
    rows.push(renderKtsDetail(item));
  }
  return rows.join("");
}

function renderKtsDraftTable(extraction) {
  const items = Array.isArray(extraction?.items) ? extraction.items : [];
  if (items.length === 0) {
    return "";
  }
  return `
    <section class="kts-draft">
      <div class="section-head compact">
        <h3>KTS 中间表</h3>
        <span class="muted">${escapeHtml(items.length)} 项</span>
      </div>
      <div class="kts-table">
        <div class="kts-row head">
          <span>事项</span>
          <span>状态</span>
          <span>内容摘要</span>
          <span>证据</span>
        </div>
        ${renderKtsDraftRows(items)}
      </div>
    </section>
  `;
}

function renderDocumentSummary(result) {
  const current = result.current || {};
  const currentResult = current.result || {};
  const documents = Array.isArray(currentResult.documents) ? currentResult.documents : [];
  return `
    <div class="status-line">
      <strong>${escapeHtml(labelForStatus(currentResult.status))}</strong>
      <span>${escapeHtml(currentResult.message || "文件解析已完成。")}</span>
    </div>
    <div class="document-list">
      ${documents
        .map((document) => {
          if (document.status === "error") {
            return `
              <article class="document-item error">
                <div class="document-item-head">
                  <div>
                    <h4>${escapeHtml(document.file_name || "未命名文件")}</h4>
                    <p class="muted">${escapeHtml(formatBytes(document.file_size))}</p>
                  </div>
                  <span class="pill warn">未解析</span>
                </div>
                <p>${escapeHtml(document.error || "文件读取失败。")}</p>
              </article>
            `;
          }
          return `
            <article class="document-item">
              <div class="document-item-head">
                <div>
                  <h4>${escapeHtml(document.file_name || "未命名文件")}</h4>
                  <p class="muted">${escapeHtml(formatBytes(document.file_size))}</p>
                </div>
                <span class="pill">${escapeHtml(document.document_type?.label || "交易文件")}</span>
              </div>
              <p class="document-status">正文及表格已读取，可用于后续生成 KTS。</p>
            </article>
          `;
        })
        .join("")}
    </div>
    ${renderEvidenceSummary(current)}
    ${renderKtsDraftTable(current.kts_extraction)}
  `;
}

function renderSelectedFiles() {
  const files = Array.from($("#documentFiles").files || []);
  if (files.length === 0) {
    $("#fileList").textContent = "尚未选择文件。";
    return;
  }
  $("#fileList").innerHTML = files
    .map((file) => `<span>${escapeHtml(file.name)} · ${escapeHtml(formatBytes(file.size))}</span>`)
    .join("");
}

async function loadCapabilities() {
  setBadge("读取能力");
  state.capabilities = await fetchJson("/api/capabilities");
  if (state.capabilities.length > 0) {
    await selectCapability(state.capabilities[0].capability_id);
  }
  setBadge("就绪");
}

async function refreshModelStatus() {
  setModelBadge("模型检测中", "pending");
  const result = await fetchJson("/api/ai/test");
  setModelBadge(result.ok ? "模型可用" : "模型需检查", result.ok ? "ok" : "warn");
  $("#modelBadge").title = result.message || "";
}

async function checkWorkbench() {
  if (!state.activeCapabilityId) return;
  setBadge("检查中");
  const payload = {
    capability_id: state.activeCapabilityId,
    party_role: $("#partyRole").value,
    matter_notes: $("#matterNotes").value,
  };
  const result = await fetchJson("/api/workbench/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  $("#resultBox").classList.remove("empty");
  $("#resultBox").innerHTML = renderRunSummary(result);
  $("#rawResultJson").textContent = JSON.stringify(result, null, 2);
  setBadge("已连通");
}

async function uploadDocuments() {
  if (!state.activeCapabilityId) return;
  const files = Array.from($("#documentFiles").files || []);
  if (files.length === 0) {
    $("#resultBox").classList.remove("empty");
    $("#resultBox").textContent = "请先选择至少一个 Word 文件。";
    return;
  }

  setBadge("处理文件");
  const runId = createRunId();
  const stopProgress = startProcessingProgress(files.length, runId);
  const payload = new FormData();
  payload.append("run_id", runId);
  payload.append("capability_id", state.activeCapabilityId);
  payload.append("party_role", $("#partyRole").value);
  payload.append("matter_notes", $("#matterNotes").value);
  for (const file of files) {
    payload.append("documents", file, file.name);
  }

  try {
    const result = await fetchJson("/api/documents/upload", {
      method: "POST",
      body: payload,
    });
    $("#resultBox").classList.remove("empty");
    $("#resultBox").innerHTML = renderDocumentSummary(result);
    $("#rawResultJson").textContent = JSON.stringify(result, null, 2);
    setBadge(result.ok ? "处理完成" : "需检查");
  } finally {
    stopProgress();
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  $("#documentFiles").addEventListener("change", renderSelectedFiles);
  $("#uploadDocuments").addEventListener("click", () => {
    uploadDocuments().catch((error) => {
      setBadge("失败");
      $("#resultBox").textContent = error.message;
      $("#rawResultJson").textContent = error.stack || error.message;
    });
  });
  $("#checkWorkbench").addEventListener("click", () => {
    checkWorkbench().catch((error) => {
      setBadge("失败");
      $("#resultBox").textContent = error.message;
      $("#rawResultJson").textContent = error.stack || error.message;
    });
  });
  refreshModelStatus().catch((error) => {
    setModelBadge("模型需检查", "warn");
    $("#modelBadge").title = error.message;
  });
  try {
    await loadCapabilities();
  } catch (error) {
    setBadge("加载失败");
    $("#capabilityDescription").textContent = error.message;
  }
});
