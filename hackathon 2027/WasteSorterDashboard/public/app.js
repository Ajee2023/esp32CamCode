const runButton = document.querySelector("#runButton");
const systemStatus = document.querySelector("#systemStatus");
const imageStage = document.querySelector("#imageStage");
const resultDisplay = document.querySelector("#resultDisplay");
const flowList = document.querySelector("#flowList");
const cameraUrl = document.querySelector("#cameraUrl");
const lcdLine1 = document.querySelector("#lcdLine1");
const lcdLine2 = document.querySelector("#lcdLine2");
const lidButton = document.querySelector("#lidButton");

const baseFlow = [
  { title: "Ready", value: "Standby", detail: "Waiting for a test run" }
];

let state = {
  status: "idle",
  category: null,
  imageDataUrl: null,
  steps: baseFlow,
  message: "Ready"
};

loadConfig();
connectEvents();
render(state);

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  try {
    const response = await fetch("/api/run-classification", { method: "POST" });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Classification failed");
    }
  } catch (error) {
    state = {
      ...state,
      status: "error",
      message: error.message,
      steps: [...state.steps, { title: "Error", value: "Stopped", detail: error.message }]
    };
    render(state);
  } finally {
    runButton.disabled = false;
  }
});

lidButton.addEventListener("click", async () => {
  lidButton.disabled = true;
  const previousStatus = systemStatus.textContent;
  systemStatus.textContent = "Opening Lid";
  lcdLine1.textContent = lcdText("Lid");
  lcdLine2.textContent = lcdText("Opening");

  try {
    const response = await fetch("/api/lid", { method: "POST" });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Lid command failed");
    }

    systemStatus.textContent = "Lid Closed";
    lcdLine1.textContent = lcdText("Lid");
    lcdLine2.textContent = lcdText("Closed");
  } catch (error) {
    systemStatus.textContent = "Lid Error";
    lcdLine1.textContent = lcdText("Lid failed");
    lcdLine2.textContent = lcdText(error.message);
  } finally {
    setTimeout(() => {
      if (systemStatus.textContent === "Lid Closed" || systemStatus.textContent === "Lid Error") {
        systemStatus.textContent = previousStatus || statusLabel(state.status);
      }
    }, 1800);
    lidButton.disabled = false;
  }
});

async function loadConfig() {
  const response = await fetch("/api/config");
  const config = await response.json();
  cameraUrl.textContent = config.cameraCaptureUrl;
}

function connectEvents() {
  const events = new EventSource("/api/events");
  events.addEventListener("snapshot", event => {
    state = JSON.parse(event.data);
    render(state);
  });
}

function render(next) {
  systemStatus.textContent = statusLabel(next.status);
  runButton.disabled = next.status === "running";

  if (next.imageDataUrl) {
    imageStage.innerHTML = `<img src="${next.imageDataUrl}" alt="Latest waste capture" />`;
  }

  const category = next.category || (next.status === "running" ? "Working" : "Ready");
  resultDisplay.innerHTML = `
    <span class="result-label">${next.status === "complete" ? "Classification" : "Current State"}</span>
    <strong>${escapeHtml(category)}</strong>
  `;

  if (next.status === "complete" && next.category) {
    lcdLine1.textContent = lcdText("AI RESULT");
    lcdLine2.textContent = lcdText(next.category);
  } else if (next.status === "error") {
    lcdLine1.textContent = lcdText("Test failed");
    lcdLine2.textContent = lcdText(next.message);
  } else {
    const last = next.steps?.[next.steps.length - 1];
    lcdLine1.textContent = lcdText(last?.title || "Ready");
    lcdLine2.textContent = lcdText(last?.value || "Waiting");
  }

  const steps = next.steps?.length ? next.steps : baseFlow;
  flowList.innerHTML = steps.map(step => `
    <li class="flow-item ${next.status === "error" && step.title === "Error" ? "error" : ""}">
      <strong>${escapeHtml(step.title)}</strong>
      <span>${escapeHtml(step.value || "")}</span>
      <span>${escapeHtml(step.detail || "")}</span>
      ${step.at ? `<time>${new Date(step.at).toLocaleTimeString()}</time>` : ""}
    </li>
  `).join("");
}

function statusLabel(status) {
  if (status === "running") return "Running";
  if (status === "complete") return "Complete";
  if (status === "error") return "Error";
  return "Ready";
}

function lcdText(value) {
  return String(value || "").slice(0, 16).padEnd(16, " ");
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
