const state = {
  capabilities: [],
  activeCapabilityId: null,
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
  `;
}

function renderAiSummary(result) {
  return `
    <div class="status-line">
      <strong>${result.ok ? "AI 服务占位检查通过" : "AI 服务异常"}</strong>
      <span>${escapeHtml(result.message || "")}</span>
    </div>
    <dl>
      <div>
        <dt>模型</dt>
        <dd>${result.configured ? escapeHtml(result.model || "已配置") : "内置占位服务"}</dd>
      </div>
      <div>
        <dt>服务</dt>
        <dd>${result.configured ? "已连接配置服务" : "待接入真实生成服务"}</dd>
      </div>
      <div>
        <dt>配置状态</dt>
        <dd>${result.configured ? "已配置" : "使用占位配置"}</dd>
      </div>
    </dl>
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

  setBadge("解析文件");
  const payload = new FormData();
  payload.append("capability_id", state.activeCapabilityId);
  payload.append("party_role", $("#partyRole").value);
  payload.append("matter_notes", $("#matterNotes").value);
  for (const file of files) {
    payload.append("documents", file, file.name);
  }

  const result = await fetchJson("/api/documents/upload", {
    method: "POST",
    body: payload,
  });
  $("#resultBox").classList.remove("empty");
  $("#resultBox").innerHTML = renderDocumentSummary(result);
  $("#rawResultJson").textContent = JSON.stringify(result, null, 2);
  setBadge(result.ok ? "解析完成" : "需检查");
}

async function testAi() {
  setBadge("测试 AI");
  const result = await fetchJson("/api/ai/test");
  $("#resultBox").classList.remove("empty");
  $("#resultBox").innerHTML = renderAiSummary(result);
  $("#rawResultJson").textContent = JSON.stringify(result, null, 2);
  setBadge(result.ok ? "AI 可用" : "AI 异常");
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
  $("#testAi").addEventListener("click", () => {
    testAi().catch((error) => {
      setBadge("失败");
      $("#resultBox").textContent = error.message;
      $("#rawResultJson").textContent = error.stack || error.message;
    });
  });

  try {
    await loadCapabilities();
  } catch (error) {
    setBadge("加载失败");
    $("#capabilityDescription").textContent = error.message;
  }
});
