const state = {
  capabilities: [],
  activeCapabilityId: null,
  latestResult: null,
  progressTimer: null,
  ktsReviewItems: [],
  activeKtsIndex: 0,
  ktsReviewKey: "",
  ktsReviewDirty: false,
  ktsReviewSaving: false,
  ktsExporting: false,
  ktsSaveMessage: "",
};

const $ = (selector) => document.querySelector(selector);

const DOCUMENT_UPLOAD_SLOTS = [
  {
    fieldName: "spa_document",
    inputSelector: "#spaDocumentFile",
    listSelector: "#spaFileList",
    label: "增资协议（SPA）",
  },
  {
    fieldName: "sha_document",
    inputSelector: "#shaDocumentFile",
    listSelector: "#shaFileList",
    label: "股东协议（SHA）",
  },
];

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
    "v0.4-source-index": "来源线索整理",
    "v0.4-kts-candidates": "条款定位",
    "v0.4-kts-extraction": "KTS 摘要生成",
    "v0.7-kts-candidates": "条款定位",
    "v0.7-kts-extraction": "KTS 摘要生成",
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

function filenameFromContentDisposition(value) {
  const header = String(value || "");
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, ""));
    } catch {
      return encoded[1].trim().replace(/^"|"$/g, "");
    }
  }
  const plain = header.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1].trim() : "";
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
    { index: 5, label: "润色摘要" },
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
  const ktsText = progress.stage === "style_polish" && totalItems > 0
    ? `批量润色 ${totalItems} 个事项`
    : totalItems > 0
      ? `KTS 事项 ${completedItems}/${totalItems}`
      : "KTS 事项待开始";
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
  let active = true;
  button.disabled = true;
  button.textContent = "处理中";
  $("#resultBox").classList.remove("empty");
  const render = () => {
    if (!active) return;
    $("#resultBox").innerHTML = renderProcessingProgress(latestProgress);
    setBadge("处理中");
  };
  const poll = async () => {
    if (!active || polling) return;
    polling = true;
    try {
      const result = await fetchJson(`/api/runs/${encodeURIComponent(runId)}/progress`);
      if (active) {
        latestProgress = result.progress || latestProgress;
      }
    } catch (error) {
      if (active) {
        latestProgress = {
          ...latestProgress,
          message: "正在等待后台进度更新。",
        };
      }
    } finally {
      polling = false;
      render();
    }
  };
  render();
  poll();
  state.progressTimer = window.setInterval(poll, 1000);
  return () => {
    active = false;
    if (state.progressTimer) {
      window.clearInterval(state.progressTimer);
      state.progressTimer = null;
    }
    button.disabled = false;
    button.textContent = "生成 KTS";
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
        <strong>文件解析</strong>
        <span>${escapeHtml(sourceSummary?.raw_block_count ?? 0)} 个原文块，${escapeHtml(sourceSummary?.search_shard_count ?? 0)} 个检索切片</span>
      </div>
      <div>
        <strong>条款定位</strong>
        <span>${escapeHtml(candidateSummary?.candidate_item_count ?? 0)} 个事项找到来源，${escapeHtml(candidateSummary?.candidate_count ?? 0)} 条来源线索</span>
      </div>
      <div>
        <strong>AI 复核</strong>
        <span>${renderModelReviewSummary(candidateSummary)}</span>
      </div>
      <div>
        <strong>KTS 摘要</strong>
        <span>${renderExtractionSummaryText(extractionSummary)}</span>
      </div>
    </div>
  `;
}

function renderExtractionSummaryText(extractionSummary) {
  const drafted = extractionSummary?.draft_content_count ?? extractionSummary?.drafted_count ?? 0;
  const coverage = extractionSummary?.schema_coverage || {};
  const requiredCount = Number(coverage.required_field_count || 0);
  const requiredFound = Number(coverage.required_field_found_count || 0);
  if (requiredCount > 0) {
    return `${escapeHtml(drafted)} 个事项已形成摘要，关键字段 ${escapeHtml(requiredFound)}/${escapeHtml(requiredCount)}`;
  }
  return `${escapeHtml(drafted)} 个事项已形成摘要`;
}

function renderModelReviewSummary(candidateSummary) {
  const review = candidateSummary?.model_review || {};
  const reviewed = review.reviewed_item_count ?? candidateSummary?.ai_reviewed_item_count ?? 0;
  const scanned = review.scanned_item_count ?? candidateSummary?.ai_scanned_item_count ?? 0;
  const added = review.added_candidate_count ?? candidateSummary?.ai_added_candidate_count ?? 0;
  const errors = review.error_item_count ?? candidateSummary?.ai_error_count ?? 0;
  if (errors > 0) {
    return `${escapeHtml(reviewed)} 个事项已复核，${escapeHtml(scanned)} 个事项已补充检索；${escapeHtml(errors)} 个事项待重新复核`;
  }
  return `${escapeHtml(reviewed)} 个事项已复核，${escapeHtml(scanned)} 个事项已补充检索，新增 ${escapeHtml(added)} 条来源线索`;
}

function labelForReviewStatus(value) {
  const labels = {
    pending: "待确认",
    ai_reviewed: "待确认",
    confirmed: "已确认",
  };
  return labels[value] || value || "待确认";
}

function classForReviewStatus(value) {
  const classes = {
    pending: "review-pending",
    ai_reviewed: "review-pending",
    confirmed: "review-confirmed",
  };
  return classes[value] || "review-pending";
}

function renderReviewStatusPill(item) {
  const status = item.review_status || "pending";
  return `<span class="pill review-status-pill ${escapeHtml(classForReviewStatus(status))}">${escapeHtml(labelForReviewStatus(status))}</span>`;
}

function labelForConfidenceLevel(value) {
  const labels = {
    high: "高",
    medium: "中",
    low: "低",
  };
  return labels[value] || value || "低";
}

function classForConfidenceLevel(value) {
  const classes = {
    high: "confidence-high",
    medium: "confidence-medium",
    low: "confidence-low",
  };
  return classes[value] || "confidence-low";
}

function renderConfidencePill(item) {
  const level = item.confidence_level || "low";
  const label = item.confidence_label || labelForConfidenceLevel(level);
  return `<span class="pill confidence-pill ${escapeHtml(classForConfidenceLevel(level))}">${escapeHtml(label)}</span>`;
}

function labelForKtsGroup(value) {
  const labels = {
    SPA: "SPA",
    SHA: "SHA",
  };
  return labels[value] || value || "其他";
}

function protectBracketedNotes(text) {
  const notes = [];
  const prepared = text.replace(/【[^】]{1,1200}】/g, (match) => {
    const token = `@@KTS_NOTE_${notes.length}@@`;
    notes.push([token, match]);
    return token;
  });
  return { prepared, notes };
}

function restoreBracketedNotes(text, notes) {
  return notes.reduce((result, [token, note]) => result.replaceAll(token, note), text);
}

function separateNoteLines(text) {
  return text.replace(/\s*(【[^】]*注[：:][^】]*】)/g, "\n$1");
}

function isNoteLine(line) {
  const text = String(line || "").trim();
  return text.startsWith("【") && text.endsWith("】") && text.includes("注");
}

function splitReadableLines(value) {
  const text = String(value || "").trim();
  if (!text) {
    return [];
  }
  let prepared = text;
  if (!text.includes("\n")) {
    const protectedNotes = protectBracketedNotes(text.replace(/\s+/g, " "));
    prepared = protectedNotes.prepared
      .replace(/([。；;])\s*/g, "$1\n")
      .replace(/([：:])\s*(（?[一二三四五六七八九十\d]+[）.)、]?)/g, "$1\n$2")
      .replace(/\s*(（[一二三四五六七八九十\d]+）)/g, "\n$1")
      .replace(/\s*(\([0-9]+\))/g, "\n$1")
      .replace(/(其中[，,])\s*/g, "\n$1");
    prepared = separateNoteLines(restoreBracketedNotes(prepared, protectedNotes.notes));
  }
  return prepared
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function numberReadableLines(lines) {
  if (lines.length <= 1) {
    return lines.join("");
  }
  let nextNumber = 1;
  return lines.map((line) => {
    if (isNoteLine(line) || /^(\d+[.、]\s*|（[一二三四五六七八九十\d]+）|\([0-9]+\))/.test(line)) {
      return line;
    }
    const numbered = `${nextNumber}. ${line}`;
    nextNumber += 1;
    return numbered;
  }).join("\n");
}

function formatKtsContentForReview(value) {
  return numberReadableLines(splitReadableLines(value));
}

function isTableLikeEvidenceLine(line) {
  return line.includes(" | ");
}

const EVIDENCE_TOPIC_PATTERNS = [
  ["投前估值", /投前估值|估值/],
  ["投后估值", /投后估值/],
  ["融资额/投资款", /融资额|增资价款|投资款|认购价款/],
  ["投资方及认购安排", /投资方|增资方|认购|新增注册资本/],
  ["注册资本变化", /注册资本|新增注册资本/],
  ["股权结构/Cap Table", /股权结构|股本结构|Cap Table|持股比例/],
  ["签署方/协议各方", /签署方|协议各方|各方确认|甲方|乙方|丙方/],
  ["交割安排", /交割|付款通知|出资证明|工商变更|股东名册/],
  ["交割先决条件", /先决条件|交割条件|重大不利|尽职调查|法律意见书/],
  ["陈述与保证", /陈述|保证|真实|准确|完整|合法合规/],
  ["交割后承诺", /交割后|资金用途|整改|重组|实缴|持续任职/],
  ["终止/解除", /终止|解除|最后期限|long stop/i],
  ["违约责任", /违约|赔偿|补偿|责任上限|连带责任/],
  ["费用承担", /费用|税费|律师费|交易费用/],
  ["合规要求", /反腐败|反商业贿赂|廉洁|利益输送|合规/],
  ["董事会安排", /董事会|董事|委派|观察员|董事长/],
  ["保护性事项", /保护性事项|重大事项|特别决议|股东会批准|董事会批准/],
  ["优先认购权", /优先认购|新增发行|二次认购/],
  ["转让限制", /转让限制|转股限制|锁定|竞对|QIPO/i],
  ["优先购买/共同出售", /优先购买|共同出售|随售|共售|ROFR/i],
  ["反稀释", /反稀释|棘轮|加权平均|价格调整/],
  ["ESOP/股权激励", /ESOP|股权激励|期权池|员工持股|员工激励/i],
  ["信息权/审计权", /信息权|检查权|审计权|财务报表|预算/],
  ["回购权", /回购|赎回|回购价款/],
  ["分红/利润分配", /分红|利润分配|股利/],
  ["清算优先权", /清算|视同清算|优先清算|剩余财产/],
  ["其他股东权利", /领售|拖售|最惠国|全职|竞业|不竞争/],
];

function evidenceTopicLabels(text) {
  const value = String(text || "");
  const labels = [];
  for (const [label, pattern] of EVIDENCE_TOPIC_PATTERNS) {
    if (pattern.test(value)) {
      labels.push(label);
    }
  }
  return labels.slice(0, 6);
}

function formatEvidenceTextForReview(value, hasTables = false) {
  let lines = splitReadableLines(value);
  if (hasTables) {
    lines = lines.filter((line) => !isTableLikeEvidenceLine(line));
  }
  return lines.join("\n");
}

function reviewContentForItem(item) {
  const hasSavedReview = item.review_is_default === false || Boolean(item.review_updated_at);
  if (hasSavedReview) {
    return item.review_content || "";
  }
  return formatKtsContentForReview(item.review_content || item.draft_content || "");
}

function normalizeReviewStatusForUi(item) {
  const status = item.review_status || "pending";
  if (status === "confirmed" || status === "ai_reviewed") {
    return status;
  }
  return "pending";
}

function normalizeKtsReviewItem(item) {
  return {
    ...item,
    review_status: normalizeReviewStatusForUi(item),
    review_content: reviewContentForItem(item),
  };
}

function ktsReviewKeyForExtraction(extraction) {
  const items = Array.isArray(extraction?.items) ? extraction.items : [];
  const ids = items.map((item) => item.taxonomy_id || "").join("|");
  const updates = items.map((item) => item.review_updated_at || "").join("|");
  return `${extraction?.updated_at || ""}:${items.length}:${ids}:${updates}`;
}

function replaceKtsReviewState(extraction, preferredIndex = 0) {
  const items = Array.isArray(extraction?.items) ? extraction.items : [];
  state.ktsReviewItems = items.map(normalizeKtsReviewItem);
  state.ktsReviewKey = ktsReviewKeyForExtraction(extraction);
  state.activeKtsIndex = Math.min(Math.max(preferredIndex, 0), Math.max(state.ktsReviewItems.length - 1, 0));
  state.ktsReviewDirty = false;
  state.ktsReviewSaving = false;
}

function ensureKtsReviewState(extraction) {
  const key = ktsReviewKeyForExtraction(extraction);
  if (state.ktsReviewKey !== key) {
    replaceKtsReviewState(extraction);
    state.ktsSaveMessage = "";
  }
  return state.ktsReviewItems;
}

function resetKtsReviewState() {
  state.ktsReviewItems = [];
  state.activeKtsIndex = 0;
  state.ktsReviewKey = "";
  state.ktsReviewDirty = false;
  state.ktsReviewSaving = false;
  state.ktsExporting = false;
  state.ktsSaveMessage = "";
}

function countKtsReview(items) {
  const counts = {
    total: items.length,
    pending: 0,
    confirmed: 0,
    high: 0,
    medium: 0,
    low: 0,
  };
  for (const item of items) {
    const status = item.review_status || "pending";
    if (status === "confirmed") {
      counts.confirmed += 1;
    } else {
      counts.pending += 1;
    }
    const confidence = item.confidence_level || "low";
    counts[confidence] = (counts[confidence] || 0) + 1;
  }
  return counts;
}

function renderReviewSummaryContent(items) {
  const counts = countKtsReview(items);
  return `
    <div class="kts-summary-row">
      <strong>复核进度</strong>
      <span>已确认 ${escapeHtml(counts.confirmed)} / ${escapeHtml(counts.total)}</span>
      <span>待确认 ${escapeHtml(counts.pending)}</span>
    </div>
    <div class="kts-summary-row confidence">
      <strong>系统可信度</strong>
      <span>高 ${escapeHtml(counts.high)}</span>
      <span>中 ${escapeHtml(counts.medium)}</span>
      <span>低 ${escapeHtml(counts.low)}</span>
    </div>
  `;
}

function renderReviewSummary(items) {
  return `
    <div class="kts-review-summary" data-kts-review-summary>
      ${renderReviewSummaryContent(items)}
    </div>
  `;
}

function renderSystemAssessment(item) {
  const initialJudgment = item.confidence_level === "high" ? "AI 初核通过" : "待人工确认";
  return `
    <div class="kts-system-assessment">
      <div>
        <strong>系统可信度</strong>
        <span>${renderConfidencePill(item)}</span>
      </div>
      <div>
        <strong>系统初始判断</strong>
        <span>${escapeHtml(initialJudgment)}</span>
      </div>
    </div>
  `;
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

function uniqueStrings(values) {
  const seen = new Set();
  const result = [];
  for (const value of Array.isArray(values) ? values : []) {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push(text);
  }
  return result;
}

function renderClauseRefs(refs) {
  const values = uniqueStrings(refs);
  if (values.length === 0) {
    return "";
  }
  return `
    <div class="kts-detail-block">
      <strong>条款定位</strong>
      <div class="clause-ref-list">
        ${values.map((ref) => `<span>${escapeHtml(ref)}</span>`).join("")}
      </div>
    </div>
  `;
}

function fieldRiskText(field) {
  return [
    field?.status,
    field?.status_label,
    field?.value,
    field?.note,
  ].map((value) => String(value || "")).join(" ");
}

function isRiskField(field) {
  const status = String(field?.status || "");
  if (status === "not_found" || status === "unclear") {
    return true;
  }
  return /未见|未明确|不明确|待确认|需确认|缺失|冲突|不一致|无法判断|无法确认|未覆盖|遗漏|例外|但|仅/.test(
    fieldRiskText(field),
  );
}

function fieldCoverageClass(field) {
  const status = String(field?.status || "");
  if (status === "found" && isRiskField(field)) {
    return "field-risk";
  }
  const classes = {
    found: "field-found",
    not_found: "field-missing",
    unclear: "field-unclear",
    not_applicable: "field-na",
  };
  return classes[status] || "field-unclear";
}

function renderRiskField(field) {
  const displayValue = field.value || field.note || "待复核";
  const note = field.note && field.note !== displayValue ? `：${field.note}` : "";
  return `
    <div class="field-coverage-item ${escapeHtml(fieldCoverageClass(field))}">
      <div>
        <strong>${escapeHtml(field.label || "未命名字段")}</strong>
        ${field.required ? "<span>必看</span>" : ""}
      </div>
      <p>${escapeHtml(displayValue)}</p>
      <small>${escapeHtml(field.status_label || "需确认")}${escapeHtml(note)}</small>
    </div>
  `;
}

function renderFieldCoverage(coverage) {
  const fields = (Array.isArray(coverage?.fields) ? coverage.fields : []).filter(isRiskField);
  if (fields.length === 0) {
    return "";
  }
  return `
    <details class="kts-detail-block field-coverage-block" open>
      <summary>需关注字段（${escapeHtml(fields.length)} 项）</summary>
      <div class="field-coverage-list">
        ${fields.map(renderRiskField).join("")}
      </div>
    </details>
  `;
}

function renderInsightList(title, values) {
  const items = uniqueStrings(values);
  if (items.length === 0) {
    return "";
  }
  return `
    <div class="kts-detail-block">
      <strong>${escapeHtml(title)}</strong>
      <ul>
        ${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </div>
  `;
}

function renderEvidenceTables(tables) {
  if (!Array.isArray(tables) || tables.length === 0) {
    return "";
  }
  return tables
    .map((table) => {
      const rows = Array.isArray(table.rows) ? table.rows : [];
      if (rows.length === 0) {
        return "";
      }
      const headerRow = rows[0];
      const bodyRows = rows.slice(1);
      const headerCells = Array.isArray(headerRow.cells) ? headerRow.cells : [];
      return `
        <div class="evidence-table-wrap">
          <div class="evidence-table-title">表格 ${escapeHtml(table.table_index || "")}</div>
          <table class="evidence-table">
            <thead>
              <tr>
                ${headerCells.map((cell) => `<th>${escapeHtml(cell)}</th>`).join("")}
              </tr>
            </thead>
            <tbody>
              ${bodyRows
                .map((row) => {
                  const cells = Array.isArray(row.cells) ? row.cells : [];
                  return `
                    <tr>
                      ${cells.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}
                    </tr>
                  `;
                })
                .join("")}
            </tbody>
          </table>
        </div>
      `;
    })
    .join("");
}

function compactText(value, maxLength = 160) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
}

function searchableSnippet(value, maxLength = 90) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength)
    .trim();
}

function evidenceSummaryText(evidence, tables) {
  const hasTables = Array.isArray(tables) && tables.length > 0;
  const sourceText = evidence.quote || evidence.context || "";
  const topics = evidenceTopicLabels(sourceText);
  if (topics.length > 0) {
    return `该证据主要涉及：${topics.join("、")}。${hasTables ? "相关表格可在原文片段中查看。" : ""}`;
  }
  const lines = splitReadableLines(evidence.quote || evidence.context || "")
    .filter((line) => !(hasTables && isTableLikeEvidenceLine(line)));
  const summary = compactText(lines.slice(0, 2).join(" "), 180);
  if (summary) {
    return `原文片段显示：${summary}`;
  }
  if (hasTables) {
    return `相关内容位于下方表格，共 ${tables.length} 个表格片段。`;
  }
  return "未返回可摘要的原文片段。";
}

function cleanSourceLocator(value) {
  return String(value || "")
    .replace(/^原文检索[：:]\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
}

function evidenceSearchSnippet(evidence) {
  const locator = cleanSourceLocator(evidence.source_locator);
  if (locator) {
    return searchableSnippet(locator);
  }
  const quote = searchableSnippet(evidence.quote || evidence.context || "");
  return quote;
}

function evidenceSearchText(evidence, fileLabel) {
  const snippet = evidenceSearchSnippet(evidence);
  if (!snippet) {
    return fileLabel ? `定位到文件：${fileLabel}` : "";
  }
  return `在「${fileLabel || "对应文件"}」中搜索连续原文「${snippet}」`;
}

function renderSourceEvidence(evidenceItems) {
  if (!Array.isArray(evidenceItems) || evidenceItems.length === 0) {
    return "";
  }
  return `
    <details class="kts-detail-block evidence-block">
      <summary>来源证据（${escapeHtml(evidenceItems.length)} 条，点击展开）</summary>
      <div class="evidence-list">
        ${evidenceItems
          .map((evidence, index) => {
            const tables = Array.isArray(evidence.tables) ? evidence.tables : [];
            const originalText = formatEvidenceTextForReview(
              evidence.context || evidence.quote || "未返回可展示原文。",
              tables.length > 0,
            );
            const roleLabel = evidence.document_role?.label || "";
            const fileLabel = evidence.file_name || "未命名文件";
            const sourceLabel = [roleLabel, evidence.file_name || "未命名文件"]
              .filter(Boolean)
              .join(" · ");
            const summary = evidenceSummaryText(evidence, tables);
            const searchText = evidenceSearchText(evidence, fileLabel);
            return `
              <article class="evidence-item">
                <div class="evidence-head">
                  <span>${escapeHtml(sourceLabel || `来源 ${index + 1}`)}</span>
                </div>
                <div class="evidence-summary">
                  <strong>摘要</strong>
                  <p>${escapeHtml(summary)}</p>
                </div>
                ${searchText ? `
                  <div class="evidence-search">
                    <strong>检索线索</strong>
                    <span>${escapeHtml(searchText)}</span>
                  </div>
                ` : ""}
                <details class="evidence-original">
                  <summary>查看原文片段</summary>
                  ${originalText ? `<p>${escapeHtml(originalText)}</p>` : ""}
                  ${renderEvidenceTables(tables)}
                </details>
              </article>
            `;
          })
          .join("")}
      </div>
    </details>
  `;
}

function renderKtsActionBar(index, total) {
  const isFirst = index <= 0;
  const isLast = index >= total - 1;
  const isSaving = state.ktsReviewSaving;
  const confirmLabel = isLast ? "确认本事项" : "确认并下一项";
  const clearLabel = isLast ? "不涉及，清空内容" : "不涉及，清空并下一项";
  return `
    <div class="kts-page-actions">
      <button type="button" class="secondary" data-action="kts-prev" ${isFirst || isSaving ? "disabled" : ""}>上一项</button>
      <button type="button" class="secondary" data-action="kts-next" ${isLast || isSaving ? "disabled" : ""}>下一项</button>
      <button type="button" data-action="kts-confirm-next" ${isSaving ? "disabled" : ""}>${escapeHtml(isSaving ? "保存中" : confirmLabel)}</button>
      <button type="button" class="secondary" data-action="kts-clear-next" ${isSaving ? "disabled" : ""}>${escapeHtml(clearLabel)}</button>
    </div>
  `;
}

function renderKtsReviewPage() {
  const items = state.ktsReviewItems;
  if (items.length === 0) {
    return "";
  }
  const index = Math.min(Math.max(state.activeKtsIndex, 0), items.length - 1);
  const item = items[index];
  const clauseRefs = renderClauseRefs(item.clause_refs);
  const coverage = renderFieldCoverage(item.field_coverage);
  const lawyerNotes = renderInsightList("律师提示", item.lawyer_notes);
  const missingOrUnclear = renderInsightList("待确认/缺失", item.missing_or_unclear);
  const notes = renderReviewNotes(item.review_notes);
  const evidence = renderSourceEvidence(item.source_evidence);
  return `
    <article class="kts-page kts-review-item" data-taxonomy-id="${escapeHtml(item.taxonomy_id || "")}">
      <div class="kts-page-head">
        <div>
          <span class="kts-page-count">第 ${escapeHtml(index + 1)} / ${escapeHtml(items.length)} 项 · ${escapeHtml(labelForKtsGroup(item.group || "OTHER"))}</span>
          <h4>${escapeHtml(item.label || item.taxonomy_id || "未命名事项")}</h4>
        </div>
        <div class="kts-page-status">
          <span class="muted">复核状态</span>
          ${renderReviewStatusPill(item)}
        </div>
      </div>
      ${renderSystemAssessment(item)}
      <div class="kts-review-controls">
        <label class="wide">
          内容摘要
          <textarea data-kts-field="review_content" rows="9">${escapeHtml(item.review_content || "")}</textarea>
        </label>
      </div>
      ${renderKtsActionBar(index, items.length)}
      <div class="kts-page-support">
        ${clauseRefs}
        ${coverage}
        ${lawyerNotes}
        ${missingOrUnclear}
        ${notes}
        ${evidence}
      </div>
    </article>
  `;
}

function renderKtsDraftTable(extraction) {
  const items = ensureKtsReviewState(extraction);
  if (items.length === 0) {
    return "";
  }
  return `
    <section class="kts-draft">
      <div class="section-head compact">
        <h3>KTS 逐项复核</h3>
        <div class="section-actions">
          <span class="muted">${escapeHtml(items.length)} 项</span>
          <span class="muted" data-kts-save-status>${escapeHtml(ktsSaveStatusText())}</span>
          <button
            type="button"
            class="secondary"
            data-action="kts-export-docx"
            ${state.ktsReviewSaving || state.ktsExporting ? "disabled" : ""}
          >
            ${escapeHtml(state.ktsExporting ? "导出中" : "导出 Word")}
          </button>
        </div>
      </div>
      ${renderReviewSummary(items)}
      <div class="kts-review-shell" data-kts-review-shell>
        ${renderKtsReviewPage()}
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
            const roleLabel = document.document_role?.label || "交易文件";
            return `
              <article class="document-item error">
                <div class="document-item-head">
                  <div>
                    <h4>${escapeHtml(roleLabel)}</h4>
                    <p class="muted">${escapeHtml(document.file_name || "未命名文件")} · ${escapeHtml(formatBytes(document.file_size))}</p>
                  </div>
                  <span class="pill warn">未解析</span>
                </div>
                <p>${escapeHtml(document.error || "文件读取失败。")}</p>
              </article>
            `;
          }
          const roleLabel = document.document_role?.label || "交易文件";
          return `
            <article class="document-item">
              <div class="document-item-head">
                <div>
                  <h4>${escapeHtml(roleLabel)}</h4>
                  <p class="muted">${escapeHtml(document.file_name || "未命名文件")} · ${escapeHtml(formatBytes(document.file_size))}</p>
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
  for (const slot of DOCUMENT_UPLOAD_SLOTS) {
    const input = $(slot.inputSelector);
    const file = input?.files?.[0];
    const list = $(slot.listSelector);
    if (!list) continue;
    if (!file) {
      list.textContent = "尚未选择文件。";
      continue;
    }
    list.innerHTML = `<span>${escapeHtml(file.name)} · ${escapeHtml(formatBytes(file.size))}</span>`;
  }
}

function selectedUploadFiles() {
  return DOCUMENT_UPLOAD_SLOTS.map((slot) => ({
    ...slot,
    file: $(slot.inputSelector)?.files?.[0] || null,
  }));
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
  };
  const result = await fetchJson("/api/workbench/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.latestResult = result;
  $("#resultBox").classList.remove("empty");
  $("#resultBox").innerHTML = renderRunSummary(result);
  setBadge("已连通");
}

async function uploadDocuments() {
  if (!state.activeCapabilityId) return;
  const selectedFiles = selectedUploadFiles();
  const missingSlots = selectedFiles.filter((item) => !item.file);
  if (missingSlots.length > 0) {
    $("#resultBox").classList.remove("empty");
    $("#resultBox").textContent = `请分别选择${missingSlots.map((item) => item.label).join("、")}。`;
    return;
  }

  setBadge("处理文件");
  const runId = createRunId();
  const stopProgress = startProcessingProgress(selectedFiles.length, runId);
  let progressStopped = false;
  const stopProcessingProgress = () => {
    if (progressStopped) return;
    progressStopped = true;
    stopProgress();
  };
  const payload = new FormData();
  payload.append("run_id", runId);
  payload.append("capability_id", state.activeCapabilityId);
  for (const item of selectedFiles) {
    payload.append(item.fieldName, item.file, item.file.name);
  }

  try {
    const result = await fetchJson("/api/documents/upload", {
      method: "POST",
      body: payload,
    });
    stopProcessingProgress();
    resetKtsReviewState();
    state.latestResult = result;
    $("#resultBox").classList.remove("empty");
    $("#resultBox").innerHTML = renderDocumentSummary(result);
    setBadge(result.ok ? "处理完成" : "需检查");
  } finally {
    stopProcessingProgress();
  }
}

async function resumeKtsReview() {
  const button = $("#resumeKtsReview");
  button.disabled = true;
  button.textContent = "正在恢复";
  setBadge("恢复中");
  try {
    const result = await fetchJson("/api/kts-review/current");
    if (!result.ok) {
      resetKtsReviewState();
      state.latestResult = null;
      $("#resultBox").classList.add("empty");
      $("#resultBox").textContent = result.message || "暂无可继续的复核结果。";
      setBadge("未开始");
      return;
    }

    resetKtsReviewState();
    state.latestResult = result;
    $("#resultBox").classList.remove("empty");
    $("#resultBox").innerHTML = renderDocumentSummary(result);
    setBadge("已恢复");
  } finally {
    button.disabled = false;
    button.textContent = "继续上次复核";
  }
}

function updateKtsSaveStatus() {
  const target = document.querySelector("[data-kts-save-status]");
  if (!target) return;
  target.textContent = ktsSaveStatusText();
}

function ktsSaveStatusText() {
  return state.ktsSaveMessage || (state.ktsReviewDirty ? "修改后点击确认保存" : "点击确认即保存");
}

function markKtsReviewDirty() {
  state.ktsReviewDirty = true;
  state.ktsSaveMessage = "修改后点击确认保存";
  updateKtsSaveStatus();
}

function updateKtsReviewSummary() {
  const target = document.querySelector("[data-kts-review-summary]");
  if (!target) return;
  target.innerHTML = renderReviewSummaryContent(state.ktsReviewItems);
}

function updateKtsReviewShell() {
  const target = document.querySelector("[data-kts-review-shell]");
  if (!target) return;
  target.innerHTML = renderKtsReviewPage();
  updateKtsReviewSummary();
  updateKtsSaveStatus();
}

function setActiveKtsIndex(nextIndex) {
  if (state.ktsReviewItems.length === 0) return;
  state.activeKtsIndex = Math.min(Math.max(nextIndex, 0), state.ktsReviewItems.length - 1);
  updateKtsReviewShell();
}

function currentKtsReviewItem() {
  return state.ktsReviewItems[state.activeKtsIndex] || null;
}

function updateCurrentKtsField(field, value) {
  const item = currentKtsReviewItem();
  if (!item || field !== "review_content") return;
  item[field] = value;
  markKtsReviewDirty();
}

function reviewPayloadForItem(item) {
  return {
    taxonomy_id: item.taxonomy_id || "",
    review_status: item.review_status || "pending",
    review_content: item.review_content || "",
  };
}

async function saveKtsReviewItems(items, preferredIndex) {
  const reviewItems = items.map(reviewPayloadForItem).filter((item) => item.taxonomy_id);
  if (reviewItems.length === 0) {
    return;
  }
  state.ktsReviewSaving = true;
  state.ktsSaveMessage = "保存中";
  updateKtsReviewShell();
  setBadge("保存中");
  let result;
  try {
    result = await fetchJson("/api/kts-review/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: reviewItems }),
    });
  } catch (error) {
    state.ktsReviewSaving = false;
    updateKtsReviewShell();
    throw error;
  }
  state.ktsReviewSaving = false;

  if (state.latestResult?.current) {
    state.latestResult.current.kts_extraction = result.current_kts_extraction;
    replaceKtsReviewState(result.current_kts_extraction, preferredIndex);
    state.ktsSaveMessage = `已保存 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    $("#resultBox").innerHTML = renderDocumentSummary(state.latestResult);
  } else {
    state.ktsReviewDirty = false;
    state.ktsSaveMessage = `已保存 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    state.activeKtsIndex = preferredIndex;
    updateKtsReviewShell();
  }
  setBadge("已保存");
}

async function confirmCurrentKtsItem(advanceAfterConfirm = false) {
  const item = currentKtsReviewItem();
  if (!item) return;
  item.review_status = "confirmed";
  const nextIndex = advanceAfterConfirm
    ? Math.min(state.activeKtsIndex + 1, state.ktsReviewItems.length - 1)
    : state.activeKtsIndex;
  await saveKtsReviewItems([item], nextIndex);
}

async function clearCurrentKtsContent(advanceAfterClear = false) {
  const item = currentKtsReviewItem();
  if (!item) return;
  item.review_content = "";
  item.review_status = "confirmed";
  const nextIndex = advanceAfterClear
    ? Math.min(state.activeKtsIndex + 1, state.ktsReviewItems.length - 1)
    : state.activeKtsIndex;
  await saveKtsReviewItems([item], nextIndex);
}

async function exportKtsDocx() {
  if (state.ktsExporting) return;
  state.ktsExporting = true;
  state.ktsSaveMessage = "导出中";
  setBadge("导出中");
  updateKtsReviewShell();
  try {
    const item = currentKtsReviewItem();
    if (state.ktsReviewDirty && item) {
      await saveKtsReviewItems([item], state.activeKtsIndex);
    }

    const response = await fetch("/api/kts-review/export-docx");
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      const contentType = response.headers.get("Content-Type") || "";
      if (contentType.includes("application/json")) {
        const errorResult = await response.json();
        message = errorResult.error || message;
      }
      throw new Error(message);
    }

    const blob = await response.blob();
    const fileName = filenameFromContentDisposition(response.headers.get("Content-Disposition"))
      || "KTS_关键条款摘要.docx";
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    state.ktsSaveMessage = `已导出 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    setBadge("已导出");
  } finally {
    state.ktsExporting = false;
    updateKtsReviewShell();
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  for (const slot of DOCUMENT_UPLOAD_SLOTS) {
    $(slot.inputSelector)?.addEventListener("change", renderSelectedFiles);
  }
  document.addEventListener("click", (event) => {
    const actionTarget = event.target?.closest?.("[data-action]");
    const action = actionTarget?.dataset?.action;
    if (action === "kts-prev") {
      setActiveKtsIndex(state.activeKtsIndex - 1);
      return;
    }
    if (action === "kts-next") {
      setActiveKtsIndex(state.activeKtsIndex + 1);
      return;
    }
    if (action === "kts-confirm-next") {
      confirmCurrentKtsItem(true).catch((error) => {
        console.error(error);
        setBadge("失败");
        state.ktsSaveMessage = "保存失败";
        updateKtsSaveStatus();
      });
      return;
    }
    if (action === "kts-clear-next") {
      clearCurrentKtsContent(true).catch((error) => {
        console.error(error);
        setBadge("失败");
        state.ktsSaveMessage = "保存失败";
        updateKtsSaveStatus();
      });
      return;
    }
    if (action === "kts-export-docx") {
      exportKtsDocx().catch((error) => {
        console.error(error);
        setBadge("失败");
        state.ktsSaveMessage = "导出失败";
        updateKtsSaveStatus();
      });
    }
  });
  document.addEventListener("input", (event) => {
    const target = event.target?.closest?.("[data-kts-field]");
    if (!target) return;
    updateCurrentKtsField(target.dataset.ktsField, target.value);
  });
  $("#uploadDocuments").addEventListener("click", () => {
    uploadDocuments().catch((error) => {
      console.error(error);
      setBadge("失败");
      $("#resultBox").textContent = error.message;
    });
  });
  $("#checkWorkbench").addEventListener("click", () => {
    checkWorkbench().catch((error) => {
      console.error(error);
      setBadge("失败");
      $("#resultBox").textContent = error.message;
    });
  });
  $("#resumeKtsReview").addEventListener("click", () => {
    resumeKtsReview().catch((error) => {
      console.error(error);
      setBadge("失败");
      $("#resultBox").classList.remove("empty");
      $("#resultBox").textContent = error.message;
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
