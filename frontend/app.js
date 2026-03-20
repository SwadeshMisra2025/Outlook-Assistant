const API_BASE = "http://127.0.0.1:8010";

const queryEl = document.getElementById("query");
const statusEl = document.getElementById("status");
const answerEl = document.getElementById("answer");
const metaEl = document.getElementById("meta");
const metricsMetaEl = document.getElementById("metrics-meta");
const sessionIdEl = document.getElementById("session-id");
const chatMessageEl = document.getElementById("chat-message");
const chatHistoryEl = document.getElementById("chat-history");
const feedbackScoreEl = document.getElementById("feedback-score");
const feedbackScoreValueEl = document.getElementById("feedback-score-value");
const feedbackCommentEl = document.getElementById("feedback-comment");
const architectureJsonEl = document.getElementById("architecture-json");
const tabWorkbenchBtn = document.getElementById("tab-workbench");
const tabArchitectureBtn = document.getElementById("tab-architecture");
const tabTechnologyBtn = document.getElementById("tab-technology");
const tabAdminBtn = document.getElementById("tab-admin");
const panelWorkbench = document.getElementById("panel-workbench");
const panelArchitecture = document.getElementById("panel-architecture");
const panelTechnology = document.getElementById("panel-technology");
const panelAdmin = document.getElementById("panel-admin");
const architectureStatusEl = document.getElementById("architecture-status");
const technologyStatusEl = document.getElementById("technology-status");
const technologyFlowEl = document.getElementById("technology-flow");
const adminStatusEl = document.getElementById("admin-status");
const adminJsonEl = document.getElementById("admin-json");
const loadModeEl = document.getElementById("load-mode");

let senderChart;
let emailMixChart;
let meetingMixChart;
let completenessChart;
let activeSessionId = null;
let lastQueryId = null;

function renderBarChart(canvasId, labels, values, label) {
  if (typeof Chart === "undefined") {
    return;
  }
  const ctx = document.getElementById(canvasId);
  if (!ctx) {
    return;
  }
  const previous = canvasId === "sender-chart" ? senderChart : canvasId === "completeness-chart" ? completenessChart : null;
  if (previous) {
    previous.destroy();
  }
  const chart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label,
          data: values,
          backgroundColor: "rgba(217, 93, 57, 0.65)",
          borderColor: "rgba(217, 93, 57, 1)",
          borderWidth: 1,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true },
      },
    },
  });
  if (canvasId === "sender-chart") {
    senderChart = chart;
  } else if (canvasId === "completeness-chart") {
    completenessChart = chart;
  }
}

function renderDoughnutChart(canvasId, oneToOne, group, titleColor) {
  if (typeof Chart === "undefined") {
    return;
  }
  const ctx = document.getElementById(canvasId);
  if (!ctx) {
    return;
  }

  let previous;
  if (canvasId === "email-mix-chart") {
    previous = emailMixChart;
  } else if (canvasId === "meeting-mix-chart") {
    previous = meetingMixChart;
  }
  if (previous) {
    previous.destroy();
  }

  const chart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["One-to-One", "Group"],
      datasets: [
        {
          data: [oneToOne, group],
          backgroundColor: [titleColor, "rgba(42, 157, 143, 0.7)"],
          borderColor: ["rgba(18, 38, 58, 0.15)", "rgba(18, 38, 58, 0.15)"],
          borderWidth: 1,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: "bottom" },
      },
    },
  });

  if (canvasId === "email-mix-chart") {
    emailMixChart = chart;
  }
  if (canvasId === "meeting-mix-chart") {
    meetingMixChart = chart;
  }
}

async function runSearch() {
  const query = queryEl.value.trim();
  if (!query) {
    statusEl.textContent = "Enter a question first.";
    return;
  }

  statusEl.textContent = "Running hybrid search...";
  answerEl.textContent = "";
  metaEl.textContent = "";

  try {
    const res = await fetch(`${API_BASE}/api/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, top_k: 6 }),
    });
    const data = await res.json();

    answerEl.textContent = data.answer || "No answer returned.";
    metaEl.textContent = JSON.stringify(data.metadata || {}, null, 2);
    lastQueryId = data?.metadata?.query_id || null;
    statusEl.textContent = `Done. Mode: ${data.mode || "unknown"}`;
  } catch (err) {
    statusEl.textContent = `Request failed: ${err.message}`;
  }
}

async function checkHealth() {
  statusEl.textContent = "Checking backend health...";
  try {
    const res = await fetch(`${API_BASE}/api/health`);
    const data = await res.json();
    statusEl.textContent = `Healthy: ${data.status} at ${data.timestamp}`;
  } catch (err) {
    statusEl.textContent = `Health check failed: ${err.message}`;
  }
}

async function loadMetrics() {
  statusEl.textContent = "Loading metrics...";
  try {
    const res = await fetch(`${API_BASE}/api/metrics?top_n=10`);
    const data = await res.json();

    const senderRows = data.emails_by_sender || [];
    renderBarChart(
      "sender-chart",
      senderRows.map((x) => x.sender),
      senderRows.map((x) => x.count),
      "Email Count"
    );

    const emailMix = data.email_participant_mix || { one_to_one: 0, group: 0 };
    renderDoughnutChart("email-mix-chart", emailMix.one_to_one, emailMix.group, "rgba(217, 93, 57, 0.8)");

    const meetingMix = data.meeting_participant_mix || { one_to_one: 0, group: 0 };
    renderDoughnutChart("meeting-mix-chart", meetingMix.one_to_one, meetingMix.group, "rgba(233, 196, 106, 0.85)");

    metricsMetaEl.textContent = JSON.stringify(
      {
        source: data.source,
        generated_at: data.generated_at,
        meta: data.meta,
      },
      null,
      2
    );

    statusEl.textContent = "Metrics loaded.";
  } catch (err) {
    statusEl.textContent = `Metrics load failed: ${err.message}`;
  }
}

async function submitFeedback() {
  if (!lastQueryId) {
    statusEl.textContent = "Run a search first to collect a query_id for feedback.";
    return;
  }

  statusEl.textContent = "Submitting feedback...";
  try {
    const score = Number(feedbackScoreEl.value);
    const comment = feedbackCommentEl.value.trim();
    const res = await fetch(`${API_BASE}/api/feedback/completeness`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query_id: lastQueryId, score, comment }),
    });
    const data = await res.json();
    statusEl.textContent = `Feedback saved for query ${data.query_id}.`;
  } catch (err) {
    statusEl.textContent = `Feedback submit failed: ${err.message}`;
  }
}

async function loadCompletenessMetrics() {
  statusEl.textContent = "Loading completeness chart...";
  try {
    const res = await fetch(`${API_BASE}/api/metrics/completeness`);
    const data = await res.json();
    const dist = data.distribution || { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 };
    renderBarChart(
      "completeness-chart",
      ["1", "2", "3", "4", "5"],
      [dist["1"], dist["2"], dist["3"], dist["4"], dist["5"]],
      "Feedback Count"
    );
    statusEl.textContent = `Completeness loaded. Avg score: ${data.average_score ?? "n/a"}`;
  } catch (err) {
    statusEl.textContent = `Completeness load failed: ${err.message}`;
  }
}

async function sendChatMessage() {
  const message = chatMessageEl.value.trim();
  if (!message) {
    statusEl.textContent = "Enter a chat message first.";
    return;
  }

  statusEl.textContent = "Sending chat message...";
  try {
    const res = await fetch(`${API_BASE}/api/chat/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: activeSessionId }),
    });
    const data = await res.json();

    activeSessionId = data.session_id;
    sessionIdEl.textContent = activeSessionId;
    chatHistoryEl.textContent = (data.history || [])
      .map((m) => `${m.role.toUpperCase()}: ${m.content}`)
      .join("\n\n");
    chatMessageEl.value = "";
    statusEl.textContent = "Chat updated.";
  } catch (err) {
    statusEl.textContent = `Chat failed: ${err.message}`;
  }
}

function setTab(tabName) {
  const showWorkbench = tabName === "workbench";
  const showArchitecture = tabName === "architecture";
  const showTechnology = tabName === "technology";
  const showAdmin = tabName === "admin";

  panelWorkbench.classList.toggle("active", showWorkbench);
  panelArchitecture.classList.toggle("active", showArchitecture);
  panelTechnology.classList.toggle("active", showTechnology);
  panelAdmin.classList.toggle("active", showAdmin);

  tabWorkbenchBtn.classList.toggle("active", showWorkbench);
  tabArchitectureBtn.classList.toggle("active", showArchitecture);
  tabTechnologyBtn.classList.toggle("active", showTechnology);
  tabAdminBtn.classList.toggle("active", showAdmin);
}

async function loadArchitecture() {
  statusEl.textContent = "Loading architecture...";
  architectureStatusEl.textContent = "Loading architecture from backend...";
  architectureJsonEl.textContent = "";
  try {
    const res = await fetch(`${API_BASE}/api/architecture`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    architectureJsonEl.textContent = JSON.stringify(data, null, 2);
    statusEl.textContent = "Architecture loaded.";
    architectureStatusEl.textContent = `Loaded at ${new Date().toLocaleTimeString()}.`;
  } catch (err) {
    statusEl.textContent = `Architecture load failed: ${err.message}`;
    architectureStatusEl.textContent = `Failed to load architecture: ${err.message}`;
  }
}

function renderTechnologyStage(stage) {
  const improvements = (stage.improvements || [])
    .map((x) => `<li>${x}</li>`)
    .join("");
  const technologies = (stage.technologies || [])
    .map((x) => `<span class=\"tech-chip\">${x}</span>`)
    .join(" ");

  return `
    <article class="tech-card">
      <h4>${stage.name}</h4>
      <p><strong>Purpose:</strong> ${stage.purpose || "-"}</p>
      <p><strong>Current:</strong> ${stage.current_state || "-"}</p>
      <div class="tech-chip-wrap">${technologies}</div>
      <p class="improve-title">Improvement Hotspots</p>
      <ul class="improve-list">${improvements}</ul>
    </article>
  `;
}

async function loadTechnologyFlow() {
  statusEl.textContent = "Loading technology flow...";
  technologyStatusEl.textContent = "Loading technology map...";
  technologyFlowEl.innerHTML = "";
  try {
    const res = await fetch(`${API_BASE}/api/technology-map`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();

    const header = `
      <article class="tech-overview">
        <h4>${data.title || "Technology Flow"}</h4>
        <p>${data.objective || ""}</p>
      </article>
    `;

    const cards = (data.stages || []).map(renderTechnologyStage).join("");
    const buildPath = (data.local_build_path || []).map((x) => `<li>${x}</li>`).join("");
    const buildPanel = `
      <article class="tech-card">
        <h4>Local Build Path</h4>
        <ul class="improve-list">${buildPath}</ul>
      </article>
    `;

    technologyFlowEl.innerHTML = header + cards + buildPanel;
    technologyStatusEl.textContent = `Loaded at ${new Date().toLocaleTimeString()}.`;
    statusEl.textContent = "Technology flow loaded.";
  } catch (err) {
    technologyStatusEl.textContent = `Failed to load technology flow: ${err.message}`;
    statusEl.textContent = `Technology flow load failed: ${err.message}`;
  }
}

async function refreshAdminStatus() {
  adminStatusEl.textContent = "Loading admin status...";
  try {
    const res = await fetch(`${API_BASE}/api/admin/load-status`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    adminJsonEl.textContent = JSON.stringify(data, null, 2);
    adminStatusEl.textContent = `Status loaded at ${new Date().toLocaleTimeString()}.`;
  } catch (err) {
    adminStatusEl.textContent = `Failed to load admin status: ${err.message}`;
  }
}

async function runAdminLoad() {
  const mode = loadModeEl.value || "incremental";
  adminStatusEl.textContent = `Running ${mode} load...`;
  statusEl.textContent = `Admin ${mode} load in progress...`;
  try {
    const res = await fetch(`${API_BASE}/api/admin/load`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    adminJsonEl.textContent = JSON.stringify(data, null, 2);
    if (data.status === "ok") {
      adminStatusEl.textContent = `${mode} load completed.`;
      statusEl.textContent = "Admin load completed.";
    } else {
      adminStatusEl.textContent = `${mode} load failed: ${data.message || "unknown error"}`;
      statusEl.textContent = "Admin load failed.";
    }
  } catch (err) {
    adminStatusEl.textContent = `Admin load failed: ${err.message}`;
    statusEl.textContent = "Admin load failed.";
  }
}

feedbackScoreEl.addEventListener("input", () => {
  feedbackScoreValueEl.textContent = String(feedbackScoreEl.value);
});

document.getElementById("run-search").addEventListener("click", runSearch);
document.getElementById("health-check").addEventListener("click", checkHealth);
document.getElementById("load-metrics").addEventListener("click", loadMetrics);
document.getElementById("submit-feedback").addEventListener("click", submitFeedback);
document.getElementById("load-completeness").addEventListener("click", loadCompletenessMetrics);
document.getElementById("send-chat").addEventListener("click", sendChatMessage);
document.getElementById("load-architecture").addEventListener("click", loadArchitecture);
document.getElementById("load-technology").addEventListener("click", loadTechnologyFlow);
document.getElementById("run-admin-load").addEventListener("click", runAdminLoad);
document.getElementById("refresh-admin-status").addEventListener("click", refreshAdminStatus);
tabWorkbenchBtn.addEventListener("click", () => setTab("workbench"));
tabArchitectureBtn.addEventListener("click", () => setTab("architecture"));
tabTechnologyBtn.addEventListener("click", () => setTab("technology"));
tabAdminBtn.addEventListener("click", () => setTab("admin"));
