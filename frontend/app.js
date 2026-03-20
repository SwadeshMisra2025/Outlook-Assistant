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

feedbackScoreEl.addEventListener("input", () => {
  feedbackScoreValueEl.textContent = String(feedbackScoreEl.value);
});

document.getElementById("run-search").addEventListener("click", runSearch);
document.getElementById("health-check").addEventListener("click", checkHealth);
document.getElementById("load-metrics").addEventListener("click", loadMetrics);
document.getElementById("submit-feedback").addEventListener("click", submitFeedback);
document.getElementById("load-completeness").addEventListener("click", loadCompletenessMetrics);
document.getElementById("send-chat").addEventListener("click", sendChatMessage);
