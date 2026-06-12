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
  };
  return labels[value] || value || "未设置";
}

function labelForPhase(value) {
  const labels = {
    "v0.2-dry-run": "工作台连通性测试",
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
  const run = result.run || {};
  const runResult = run.result || {};
  const capability = runResult.capability || {};
  return `
    <div class="status-line">
      <strong>${escapeHtml(labelForStatus(runResult.status) || "已记录")}</strong>
      <span>${escapeHtml(runResult.message || "运行已完成。")}</span>
    </div>
    <dl>
      <div>
        <dt>运行 ID</dt>
        <dd>${escapeHtml(run.run_id || "未生成")}</dd>
      </div>
      <div>
        <dt>能力</dt>
        <dd>${escapeHtml(capability.title || "融资交易 KTS")}</dd>
      </div>
      <div>
        <dt>阶段</dt>
        <dd>${escapeHtml(labelForPhase(run.phase))}</dd>
      </div>
      <div>
        <dt>创建时间</dt>
        <dd>${escapeHtml(run.created_at || "未记录")}</dd>
      </div>
    </dl>
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

async function loadCapabilities() {
  setBadge("读取能力");
  state.capabilities = await fetchJson("/api/capabilities");
  if (state.capabilities.length > 0) {
    await selectCapability(state.capabilities[0].capability_id);
  }
  setBadge("就绪");
}

async function runDryRun() {
  if (!state.activeCapabilityId) return;
  setBadge("运行中");
  const payload = {
    capability_id: state.activeCapabilityId,
    template_mode: "single_round",
    party_role: $("#partyRole").value,
    matter_notes: $("#matterNotes").value,
  };
  const result = await fetchJson("/api/runs/dry-run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  $("#resultBox").classList.remove("empty");
  $("#resultBox").innerHTML = renderRunSummary(result);
  $("#rawResultJson").textContent = JSON.stringify(result, null, 2);
  setBadge("完成");
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
  $("#dryRun").addEventListener("click", () => {
    runDryRun().catch((error) => {
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
