const API_BASE = "http://127.0.0.1:8010";

const queryEl = document.getElementById("query");
const recentQueriesEl = document.getElementById("recent-queries");
const statusEl = document.getElementById("status");
const answerEl = document.getElementById("answer");
const metaEl = document.getElementById("meta");
const rawResultsEl = document.getElementById("raw-results");
const evidenceToggleEl = document.getElementById("evidence-toggle");
const evidenceDrawerEl = document.getElementById("evidence-drawer");
const evidenceCloseEl = document.getElementById("evidence-close");
const evidenceCountEl = document.getElementById("evidence-count");
const metricsMetaEl = document.getElementById("metrics-meta");
const sessionIdEl = document.getElementById("session-id");
const chatMessageEl = document.getElementById("chat-message");
const chatHistoryEl = document.getElementById("chat-history");
const newChatSessionEl = document.getElementById("new-chat-session");
const chatBoxEl = document.querySelector(".chat-box");
const chatMinimizeBtnEl = document.getElementById("chat-minimize-btn");
const metricsFloatEl = document.querySelector(".metrics-float");
const metricsMinimizeBtnEl = document.getElementById("metrics-minimize-btn");
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
const RECENT_QUERIES_KEY = "oa_recent_queries_v1";
const CHAT_SESSION_KEY = "oa_chat_session_v1";

function loadStoredChatSessionId() {
  try {
    return localStorage.getItem(CHAT_SESSION_KEY) || null;
  } catch {
    return null;
  }
}

function storeChatSessionId(sessionId) {
  try {
    if (sessionId) {
      localStorage.setItem(CHAT_SESSION_KEY, sessionId);
    } else {
      localStorage.removeItem(CHAT_SESSION_KEY);
    }
  } catch {
    // Ignore storage failures.
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderChatHistory(history) {
  if (!chatHistoryEl) {
    return;
  }
  if (!Array.isArray(history) || history.length === 0) {
    chatHistoryEl.innerHTML = '<p class="chat-empty">No messages yet.</p>';
    return;
  }

  chatHistoryEl.innerHTML = history
    .map((message) => {
      const role = message.role === "assistant" ? "assistant" : "user";
      const label = role === "assistant" ? "Assistant" : "You";
      const content = escapeHtml(message.content).replaceAll("\n", "<br>");
      return `
        <article class="chat-message ${role}">
          <p class="chat-role">${label}</p>
          <div class="chat-bubble">${content}</div>
        </article>
      `;
    })
    .join("");

  chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
}

async function restoreChatSession() {
  activeSessionId = loadStoredChatSessionId();
  if (!activeSessionId) {
    sessionIdEl.textContent = "(new session)";
    renderChatHistory([]);
    return;
  }

  sessionIdEl.textContent = activeSessionId;
  try {
    const res = await fetch(`${API_BASE}/api/chat/session/${activeSessionId}`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    renderChatHistory(data.history || []);
    statusEl.textContent = "Conversation restored.";
  } catch (err) {
    activeSessionId = null;
    storeChatSessionId(null);
    sessionIdEl.textContent = "(new session)";
    renderChatHistory([]);
    statusEl.textContent = `Previous conversation could not be restored: ${err.message}`;
  }
}

function startNewConversation() {
  activeSessionId = null;
  storeChatSessionId(null);
  sessionIdEl.textContent = "(new session)";
  renderChatHistory([]);
  chatMessageEl.value = "";
  chatMessageEl.focus();
  statusEl.textContent = "Started a new conversation.";
}

function getRecentQueries() {
  try {
    const raw = localStorage.getItem(RECENT_QUERIES_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter((x) => typeof x === "string" && x.trim()).slice(0, 5);
  } catch {
    return [];
  }
}

function setRecentQueries(queries) {
  localStorage.setItem(RECENT_QUERIES_KEY, JSON.stringify(queries.slice(0, 5)));
}

function renderRecentQueries() {
  const queries = getRecentQueries();
  if (!recentQueriesEl) {
    return;
  }

  recentQueriesEl.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Select one of your last 5 questions...";
  recentQueriesEl.appendChild(placeholder);

  queries.forEach((q) => {
    const option = document.createElement("option");
    option.value = q;
    option.textContent = q.length > 120 ? `${q.slice(0, 117)}...` : q;
    recentQueriesEl.appendChild(option);
  });
}

function saveRecentQuery(query) {
  const normalized = query.trim();
  if (!normalized) {
    return;
  }
  const existing = getRecentQueries().filter((x) => x.toLowerCase() !== normalized.toLowerCase());
  const updated = [normalized, ...existing].slice(0, 5);
  setRecentQueries(updated);
  renderRecentQueries();
}

function renderRawResults(results) {
  if (!Array.isArray(results) || results.length === 0) {
    return "No raw evidence rows returned for this query.";
  }

  const lines = results.map((item, idx) => {
    const source = item.source_type || item.type || "unknown";
    const title = item.title || "(no title)";
    const eventTime = item.event_time || "(no timestamp)";
    const section = item.section || "General";
    return [
      `${idx + 1}. [${source}] ${title}`,
      `   section: ${section}`,
      `   time: ${eventTime}`,
    ]
      .filter(Boolean)
      .join("\n");
  });

  return lines.join("\n\n");
}

function setEvidenceDrawer(open) {
  if (!evidenceDrawerEl || !evidenceToggleEl) {
    return;
  }
  evidenceDrawerEl.classList.toggle("open", open);
  evidenceDrawerEl.setAttribute("aria-hidden", open ? "false" : "true");
  evidenceToggleEl.setAttribute("aria-expanded", open ? "true" : "false");
}

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

async function parseApiResponse(res) {
  const rawText = await res.text();
  let data = null;

  if (rawText) {
    try {
      data = JSON.parse(rawText);
    } catch {
      const preview = rawText.length > 220 ? `${rawText.slice(0, 220)}...` : rawText;
      throw new Error(`HTTP ${res.status}: ${preview}`);
    }
  }

  if (!res.ok) {
    const message = data?.detail || data?.message || `HTTP ${res.status}`;
    throw new Error(message);
  }

  return data || {};
}

async function runSearch() {
  const query = queryEl.value.trim();
  if (!query) {
    statusEl.textContent = "Enter a question first.";
    return;
  }

  saveRecentQuery(query);

  statusEl.textContent = "Running hybrid search...";
  answerEl.textContent = "";
  metaEl.textContent = "";
  rawResultsEl.textContent = "Loading evidence...";

  try {
    const res = await fetch(`${API_BASE}/api/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, top_k: 250 }),
    });
    const data = await parseApiResponse(res);

    answerEl.textContent = data.answer || "No answer returned.";
    metaEl.textContent = JSON.stringify(data.metadata || {}, null, 2);
    rawResultsEl.textContent = renderRawResults(data.results || []);
    if (evidenceCountEl) {
      evidenceCountEl.textContent = String((data.results || []).length);
    }
    lastQueryId = data?.metadata?.query_id || null;
    statusEl.textContent = `Done. Mode: ${data.mode || "unknown"}. Evidence rows: ${(data.results || []).length}`;
  } catch (err) {
    statusEl.textContent = `Request failed: ${err.message}`;
    rawResultsEl.textContent = "Failed to load evidence.";
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

    const corrRows = data.emails_by_correspondent || [];
    const senderRowsRaw = data.emails_by_sender || [];
    const senderRows = corrRows.length
      ? corrRows.map((x) => ({ sender: x.person, count: x.count }))
      : senderRowsRaw.map((x) => ({
          sender: x.sender === "exchange_internal_sender" ? "exchange_internal (legacy id)" : x.sender,
          count: x.count,
        }));
    renderBarChart(
      "sender-chart",
      senderRows.map((x) => x.sender),
      senderRows.map((x) => x.count),
      corrRows.length ? "Email Correspondence Count" : "Email Count"
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

    const totalEmails = data?.meta?.total_emails ?? senderRows.reduce((acc, x) => acc + (x.count || 0), 0);
    statusEl.textContent = `Metrics loaded. Total emails read: ${totalEmails}. Sender rows plotted: ${senderRows.length}.`;
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
    storeChatSessionId(activeSessionId);
    sessionIdEl.textContent = activeSessionId;
    renderChatHistory(data.history || []);
    chatMessageEl.value = "";
    statusEl.textContent = `Chat updated. Route: ${data?.metadata?.mode || "unknown"}.`;
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
newChatSessionEl.addEventListener("click", startNewConversation);
document.getElementById("load-architecture").addEventListener("click", loadArchitecture);
document.getElementById("load-technology").addEventListener("click", loadTechnologyFlow);
document.getElementById("run-admin-load").addEventListener("click", runAdminLoad);
document.getElementById("refresh-admin-status").addEventListener("click", refreshAdminStatus);
tabWorkbenchBtn.addEventListener("click", () => setTab("workbench"));
tabArchitectureBtn.addEventListener("click", () => setTab("architecture"));
tabTechnologyBtn.addEventListener("click", () => setTab("technology"));
tabAdminBtn.addEventListener("click", () => setTab("admin"));

if (recentQueriesEl) {
  recentQueriesEl.addEventListener("change", () => {
    const selected = recentQueriesEl.value;
    if (!selected) {
      return;
    }
    queryEl.value = selected;
    queryEl.focus();
    statusEl.textContent = "Loaded a recent question into Ask.";
  });
}

if (evidenceToggleEl) {
  evidenceToggleEl.addEventListener("click", () => {
    const currentlyOpen = evidenceDrawerEl?.classList.contains("open");
    setEvidenceDrawer(!currentlyOpen);
  });
}

if (evidenceCloseEl) {
  evidenceCloseEl.addEventListener("click", () => setEvidenceDrawer(false));
}

if (chatMessageEl) {
  chatMessageEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendChatMessage();
    }
  });
}

if (chatMinimizeBtnEl && chatBoxEl) {
  chatMinimizeBtnEl.addEventListener("click", () => {
    const minimized = chatBoxEl.classList.toggle("minimized");
    chatMinimizeBtnEl.textContent = minimized ? "+" : "−";
    chatMinimizeBtnEl.setAttribute("aria-expanded", minimized ? "false" : "true");
  });
}

if (metricsMinimizeBtnEl && metricsFloatEl) {
  metricsMinimizeBtnEl.addEventListener("click", () => {
    const minimized = metricsFloatEl.classList.toggle("minimized");
    metricsMinimizeBtnEl.textContent = minimized ? "+" : "−";
    metricsMinimizeBtnEl.setAttribute("aria-expanded", minimized ? "false" : "true");
  });
}

renderRecentQueries();
setEvidenceDrawer(true);
restoreChatSession();
