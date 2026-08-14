const runButton = document.querySelector("#runButton");
const systemStatus = document.querySelector("#systemStatus");
const imageStage = document.querySelector("#imageStage");
const cameraVideo = document.querySelector("#cameraVideo");
const captureCanvas = document.querySelector("#captureCanvas");
const capturePreview = document.querySelector("#capturePreview");
const cameraPlaceholder = document.querySelector("#cameraPlaceholder");
const cameraMessage = document.querySelector("#cameraMessage");
const cameraDetail = document.querySelector("#cameraDetail");
const cameraSelect = document.querySelector("#cameraSelect");
const cameraRetryButton = document.querySelector("#cameraRetryButton");
const photoUpload = document.querySelector("#photoUpload");
const resultDisplay = document.querySelector("#resultDisplay");
const aiModeLabel = document.querySelector("#aiModeLabel");
const flowList = document.querySelector("#flowList");
const cameraUrl = document.querySelector("#cameraUrl");
const lcdLine1 = document.querySelector("#lcdLine1");
const lcdLine2 = document.querySelector("#lcdLine2");
const lidButton = document.querySelector("#lidButton");
const wheelPosition = document.querySelector("#wheelPosition");
const speedControl = document.querySelector("#speedControl");
const speedValue = document.querySelector("#speedValue");
const blueMsInput = document.querySelector("#blueMs");
const blueHomeMsInput = document.querySelector("#blueHomeMs");
const greenMsInput = document.querySelector("#greenMs");
const greenHomeMsInput = document.querySelector("#greenHomeMs");
const nudgeMsInput = document.querySelector("#nudgeMs");
const swapDirectionsInput = document.querySelector("#swapDirections");
const blueBinButton = document.querySelector("#blueBinButton");
const homeBinButton = document.querySelector("#homeBinButton");
const greenBinButton = document.querySelector("#greenBinButton");
const nudgeLeftButton = document.querySelector("#nudgeLeftButton");
const nudgeRightButton = document.querySelector("#nudgeRightButton");
const rawLeftButton = document.querySelector("#rawLeftButton");
const rawRightButton = document.querySelector("#rawRightButton");
const stopWheelButton = document.querySelector("#stopWheelButton");
const setHomeButton = document.querySelector("#setHomeButton");
const calibrationTarget = document.querySelector("#calibrationTarget");
const calibrationLeftCount = document.querySelector("#calibrationLeftCount");
const calibrationRightCount = document.querySelector("#calibrationRightCount");
const calibrationNetCount = document.querySelector("#calibrationNetCount");
const calibrationStartBlueButton = document.querySelector("#calibrationStartBlueButton");
const calibrationStartGreenButton = document.querySelector("#calibrationStartGreenButton");
const calibrationSaveButton = document.querySelector("#calibrationSaveButton");
const calibrationResetButton = document.querySelector("#calibrationResetButton");
const tabButtons = document.querySelectorAll("[data-tab-target]");
const sorterPanel = document.querySelector("#sorterPanel");
const summaryPanel = document.querySelector("#summaryPanel");
const chartPanel = document.querySelector("#chartPanel");
const refreshSummaryButton = document.querySelector("#refreshSummaryButton");
const summaryTableBody = document.querySelector("#summaryTableBody");
const summaryMessage = document.querySelector("#summaryMessage");
const refreshChartButton = document.querySelector("#refreshChartButton");
const chartMessage = document.querySelector("#chartMessage");
const chartStats = document.querySelector("#chartStats");
const classificationChart = document.querySelector("#classificationChart");
const binButtons = {
  blue: blueBinButton,
  home: homeBinButton,
  green: greenBinButton
};

const baseFlow = [
  { title: "Ready", value: "Standby", detail: "Waiting for a test run" }
];

let cameraReady = false;
let cameraError = "";
let currentStream = null;
let selectedDeviceId = "";
let state = {
  status: "idle",
  category: null,
  imageDataUrl: null,
  steps: baseFlow,
  message: "Ready"
};

const motorSettings = loadMotorSettings();
const calibrationState = {
  target: "",
  left: 0,
  right: 0
};
let motorRunId = 0;
let motorBusy = false;
let manualActiveBin = "";
let manualStepSide = "";
let manualLidActive = false;
let summaryLoaded = false;
let chartLoaded = false;
let cameraDeviceWatcherInstalled = false;

registerGlobalErrorHandlers();
window.setTimeout(() => {
  void bootApp();
}, 0);

function registerGlobalErrorHandlers() {
  window.addEventListener("error", event => {
    showBootError(event.error || new Error(event.message || "Browser script error"));
  });

  window.addEventListener("unhandledrejection", event => {
    showBootError(event.reason || new Error("Browser promise error"));
  });
}

async function bootApp() {
  try {
    render(state);
    await loadConfig();
    connectEvents();
    initWheelControls();
    initTabs();
    initSummary();
    installCameraDeviceWatcher();
    await startLaptopCamera();
  } catch (error) {
    showBootError(error);
  }
}

function showBootError(error) {
  const message = formatCameraError(error);
  cameraReady = false;
  cameraError = message;
  cameraVideo.hidden = true;
  cameraPlaceholder.hidden = false;
  setCameraPlaceholder("Camera startup failed", message);
  cameraUrl.textContent = "Browser camera failed";
  systemStatus.textContent = "Camera Error";
  runButton.disabled = true;
  cameraRetryButton.disabled = false;
  cameraSelect.disabled = cameraSelect.options.length <= 1;
  console.error("Waste sorter startup/camera error", error);
}
runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  try {
    await classifyImageDataUrl(captureCurrentFrame());
  } catch (error) {
    showRunError(error);
  } finally {
    runButton.disabled = state.status === "running" || !cameraReady || motorBusy;
    syncCalibrationUi();
  }
});

cameraRetryButton.addEventListener("click", () => {
  selectedDeviceId = cameraSelect.value || "";
  startLaptopCamera();
});

cameraSelect.addEventListener("change", () => {
  selectedDeviceId = cameraSelect.value || "";
  startLaptopCamera();
});

photoUpload.addEventListener("change", async () => {
  const file = photoUpload.files?.[0];
  if (!file) {
    return;
  }

  try {
    if (!file.type.startsWith("image/")) {
      throw new Error("Choose an image file");
    }

    const imageDataUrl = await readImageFile(file);
    await classifyImageDataUrl(imageDataUrl);
  } catch (error) {
    showRunError(error);
  } finally {
    photoUpload.value = "";
    runButton.disabled = state.status === "running" || !cameraReady || motorBusy;
    syncCalibrationUi();
  }
});

function initTabs() {
  for (const button of tabButtons) {
    button.addEventListener("click", () => activateTab(button.dataset.tabTarget));
  }
}

function activateTab(panelId) {
  const panels = [sorterPanel, summaryPanel, chartPanel].filter(Boolean);
  for (const panel of panels) {
    const isActive = panel.id === panelId;
    panel.hidden = !isActive;
    panel.classList.toggle("is-active", isActive);
  }

  for (const button of tabButtons) {
    const isActive = button.dataset.tabTarget === panelId;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", String(isActive));
  }

  if (panelId === "summaryPanel") {
    loadClassificationSummary();
  } else if (panelId === "chartPanel") {
    loadClassificationChart();
  }
}
function initSummary() {
  refreshSummaryButton?.addEventListener("click", () => loadClassificationSummary({ force: true }));
  refreshChartButton?.addEventListener("click", () => loadClassificationChart({ force: true }));
  summaryTableBody?.addEventListener("click", event => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;

    const row = button.closest("tr[data-entry-id]");
    if (!row) return;

    if (button.dataset.action === "save") {
      saveSummaryEntry(row);
    } else if (button.dataset.action === "delete") {
      deleteSummaryEntry(row);
    }
  });
}

function installCameraDeviceWatcher() {
  if (cameraDeviceWatcherInstalled || !navigator.mediaDevices?.addEventListener) {
    return;
  }

  navigator.mediaDevices.addEventListener("devicechange", () => {
    void refreshCameraDevices({ silent: true });
  });

  cameraDeviceWatcherInstalled = true;
}

async function loadClassificationSummary(options = {}) {
  if (!summaryTableBody || (!options.force && summaryLoaded && summaryPanel?.hidden)) {
    return;
  }

  summaryMessage.textContent = "Loading saved classifications";
  summaryTableBody.innerHTML = `<tr><td colspan="9" class="empty-cell">Loading classification summary...</td></tr>`;

  try {
    const response = await fetch("/api/classifications");
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Could not load classification summary");
    }

    summaryLoaded = true;
    renderClassificationSummary(result.entries || []);
  } catch (error) {
    summaryMessage.textContent = "Summary load failed";
    summaryTableBody.innerHTML = `<tr><td colspan="9" class="empty-cell error-text">${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderClassificationSummary(entries) {
  summaryMessage.textContent = entries.length
    ? `${entries.length} saved classification${entries.length === 1 ? "" : "s"}`
    : "No saved classifications yet";

  if (!entries.length) {
    summaryTableBody.innerHTML = `<tr><td colspan="9" class="empty-cell">Run a classification to create the first summary row.</td></tr>`;
    return;
  }

  summaryTableBody.innerHTML = entries.map(entry => `
    <tr data-entry-id="${entry.id}">
      <td>${escapeHtml(formatDateTime(entry.createdAt))}</td>
      <td><code>${escapeHtml(entry.aiModel || "")}</code></td>
      <td>${classificationSelect(entry.classification)}</td>
      <td>${entry.imageDataUrl ? `<img class="summary-thumb" src="${escapeHtml(entry.imageDataUrl)}" alt="Captured waste image" />` : `<span class="muted-text">No image</span>`}</td>
      <td>${confidenceLevelSelect(entry.confidenceLevel)}</td>
      <td><input class="summary-input score-input" data-field="geminiConfidenceScore" type="number" min="0" max="100" step="0.1" value="${entry.geminiConfidenceScore ?? entry.confidenceScore ?? ""}" /></td>
      <td><input class="summary-input score-input" data-field="edgeImpulseConfidenceScore" type="number" min="0" max="100" step="0.1" value="${entry.edgeImpulseConfidenceScore ?? ""}" placeholder="n/a" /></td>
      <td><textarea class="summary-input rationale-input" data-field="rationale" rows="2">${escapeHtml(entry.rationale || "")}</textarea></td>
      <td>
        <div class="row-actions">
          <button type="button" class="secondary-action compact-action" data-action="save">Save</button>
          <button type="button" class="danger-action compact-action" data-action="delete">Delete</button>
        </div>
      </td>
    </tr>
  `).join("");
}

function classificationSelect(value) {
  return `<select class="summary-input" data-field="classification">${["Garbage", "Recycle", "Organic", "Unknown"].map(option => `
    <option value="${option}"${option === value ? " selected" : ""}>${option}</option>
  `).join("")}</select>`;
}

function confidenceLevelSelect(value) {
  return `<select class="summary-input" data-field="confidenceLevel">${["High", "Medium", "Low"].map(option => `
    <option value="${option}"${option === value ? " selected" : ""}>${option}</option>
  `).join("")}</select>`;
}

async function loadClassificationChart(options = {}) {
  if (!classificationChart || (!options.force && chartLoaded && chartPanel?.hidden)) {
    return;
  }

  chartMessage.textContent = "Loading chart";
  chartStats.innerHTML = "";
  classificationChart.innerHTML = `<div class="empty-chart">Loading classification chart...</div>`;

  try {
    const response = await fetch("/api/classifications");
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Could not load classification chart");
    }

    chartLoaded = true;
    renderClassificationChart(result.entries || []);
  } catch (error) {
    chartMessage.textContent = "Chart load failed";
    chartStats.innerHTML = "";
    classificationChart.innerHTML = `<div class="empty-chart error-text">${escapeHtml(error.message)}</div>`;
  }
}

function renderClassificationChart(entries) {
  const categories = ["Garbage", "Recycle", "Organic", "Unknown"];
  const counts = Object.fromEntries(categories.map(category => [category, 0]));
  const geminiScores = [];
  const edgeScores = [];

  for (const entry of entries) {
    if (counts[entry.classification] !== undefined) {
      counts[entry.classification] += 1;
    }
    const geminiScore = Number(entry.geminiConfidenceScore ?? entry.confidenceScore);
    if (Number.isFinite(geminiScore)) {
      geminiScores.push(geminiScore);
    }
    const edgeScore = Number(entry.edgeImpulseConfidenceScore);
    if (Number.isFinite(edgeScore)) {
      edgeScores.push(edgeScore);
    }
  }

  const total = entries.length;
  const averageGemini = averageScoreText(geminiScores);
  const averageEdge = averageScoreText(edgeScores);
  const topCategory = categories.reduce((best, category) => counts[category] > counts[best] ? category : best, categories[0]);
  const latest = entries[0];

  chartMessage.textContent = total
    ? `${total} saved classification${total === 1 ? "" : "s"}`
    : "No saved classifications yet";

  chartStats.innerHTML = [
    chartStatCard("Total Runs", String(total)),
    chartStatCard("Avg Gemini", averageGemini),
    chartStatCard("Avg Edge", averageEdge),
    chartStatCard("Top Category", total ? topCategory : "n/a"),
    chartStatCard("Latest Result", latest ? `${latest.classification} (${formatDateTime(latest.createdAt)})` : "n/a")
  ].join("");

  if (!total) {
    classificationChart.innerHTML = `<div class="empty-chart">Run a classification to populate this chart.</div>`;
    return;
  }

  const maxCount = Math.max(...Object.values(counts), 1);
  classificationChart.innerHTML = categories.map(category => {
    const count = counts[category];
    const percent = Math.round((count / total) * 100);
    const width = Math.max(4, Math.round((count / maxCount) * 100));
    return `
      <div class="chart-row chart-${category.toLowerCase()}">
        <div class="chart-label"><strong>${category}</strong><span>${count} run${count === 1 ? "" : "s"}</span></div>
        <div class="chart-track"><span style="width: ${width}%"></span></div>
        <div class="chart-percent">${percent}%</div>
      </div>
    `;
  }).join("");
}

function averageScoreText(scores) {
  return scores.length
    ? `${Math.round(scores.reduce((sum, score) => sum + score, 0) / scores.length)}%`
    : "n/a";
}

function chartStatCard(label, value) {
  return `<div class="chart-stat"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

async function saveSummaryEntry(row) {
  const id = row.dataset.entryId;
  const payload = {
    classification: row.querySelector('[data-field="classification"]').value,
    confidenceLevel: row.querySelector('[data-field="confidenceLevel"]').value,
    geminiConfidenceScore: row.querySelector('[data-field="geminiConfidenceScore"]').value,
    edgeImpulseConfidenceScore: row.querySelector('[data-field="edgeImpulseConfidenceScore"]').value,
    rationale: row.querySelector('[data-field="rationale"]').value
  };

  summaryMessage.textContent = "Saving update";
  const response = await fetch(`/api/classifications/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const result = await response.json();
  if (!response.ok) {
    summaryMessage.textContent = result.error || "Save failed";
    return;
  }

  summaryMessage.textContent = `Updated entry #${result.entry.id}`;
  chartLoaded = false;
  await loadClassificationSummary({ force: true });
}

async function deleteSummaryEntry(row) {
  const id = row.dataset.entryId;
  if (!window.confirm("Delete this saved classification entry?")) {
    return;
  }

  summaryMessage.textContent = "Deleting entry";
  const response = await fetch(`/api/classifications/${encodeURIComponent(id)}`, { method: "DELETE" });
  const result = await response.json();
  if (!response.ok) {
    summaryMessage.textContent = result.error || "Delete failed";
    return;
  }

  summaryMessage.textContent = `Deleted entry #${id}`;
  chartLoaded = false;
  await loadClassificationSummary({ force: true });
}

function formatDateTime(value) {
  if (!value) return "";
  return new Date(value).toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit"
  });
}
function initWheelControls() {
  if (!speedControl) {
    return;
  }

  speedControl.value = motorSettings.speed;
  blueMsInput.value = motorSettings.blueMs;
  blueHomeMsInput.value = motorSettings.blueHomeMs;
  greenMsInput.value = motorSettings.greenMs;
  greenHomeMsInput.value = motorSettings.greenHomeMs;
  nudgeMsInput.value = motorSettings.nudgeMs;
  swapDirectionsInput.checked = motorSettings.swapDirections;
  syncWheelUi();

  for (const input of [speedControl, blueMsInput, blueHomeMsInput, greenMsInput, greenHomeMsInput, nudgeMsInput, swapDirectionsInput]) {
    input.addEventListener("input", readWheelSettingsFromUi);
    input.addEventListener("change", readWheelSettingsFromUi);
  }

  blueBinButton.addEventListener("click", () => moveWheelTo("blue"));
  homeBinButton.addEventListener("click", () => moveWheelTo("home"));
  greenBinButton.addEventListener("click", () => moveWheelTo("green"));
  nudgeLeftButton.addEventListener("click", () => nudgeWheel("left"));
  nudgeRightButton.addEventListener("click", () => nudgeWheel("right"));
  rawLeftButton.addEventListener("click", () => rawWheel("left"));
  rawRightButton.addEventListener("click", () => rawWheel("right"));
  stopWheelButton.addEventListener("click", stopWheel);
  calibrationStartBlueButton?.addEventListener("click", () => startCalibration("blue"));
  calibrationStartGreenButton?.addEventListener("click", () => startCalibration("green"));
  calibrationSaveButton?.addEventListener("click", saveCalibrationCount);
  calibrationResetButton?.addEventListener("click", resetCalibration);
  setHomeButton.addEventListener("click", () => {
    motorSettings.position = "home";
    saveMotorSettings();
    syncWheelUi();
    manualActiveBin = "home";
    syncActionHighlights(state);
    setTimeout(() => {
      manualActiveBin = "";
      syncActionHighlights(state);
    }, 1200);
    setWheelMessage("Home Set", "Garbage");
  });
}

function loadMotorSettings() {
  const defaults = {
    version: 12,
    speed: 170,
    blueMs: 7,
    blueHomeMs: 5,
    greenMs: 5,
    greenHomeMs: 6.5,
    nudgeMs: 60,
    swapDirections: false,
    position: "home"
  };

  try {
    const saved = JSON.parse(localStorage.getItem("wasteSorterMotorSettings") || "{}");
    if (saved.version !== defaults.version) {
      return defaults;
    }
    return {
      ...defaults,
      ...saved,
      version: defaults.version,
      speed: clampNumber(saved.speed, defaults.speed, 80, 255),
      blueMs: clampNumber(saved.blueMs, defaults.blueMs, 1, 40),
      blueHomeMs: clampNumber(saved.blueHomeMs, defaults.blueHomeMs, 1, 40),
      greenMs: clampNumber(saved.greenMs, defaults.greenMs, 1, 40),
      greenHomeMs: clampNumber(saved.greenHomeMs, defaults.greenHomeMs, 1, 40),
      nudgeMs: clampNumber(saved.nudgeMs, defaults.nudgeMs, 60, 1000),
      position: ["home", "blue", "green", "manual"].includes(saved.position) ? saved.position : defaults.position,
      swapDirections: Boolean(saved.swapDirections)
    };
  } catch (_error) {
    return defaults;
  }
}

function saveMotorSettings() {
  localStorage.setItem("wasteSorterMotorSettings", JSON.stringify(motorSettings));
}

function readWheelSettingsFromUi() {
  motorSettings.speed = clampNumber(speedControl.value, motorSettings.speed, 80, 255);
  motorSettings.blueMs = clampNumber(blueMsInput.value, motorSettings.blueMs, 1, 40);
  motorSettings.blueHomeMs = clampNumber(blueHomeMsInput.value, motorSettings.blueHomeMs, 1, 40);
  motorSettings.greenMs = clampNumber(greenMsInput.value, motorSettings.greenMs, 1, 40);
  motorSettings.greenHomeMs = clampNumber(greenHomeMsInput.value, motorSettings.greenHomeMs, 1, 40);
  motorSettings.nudgeMs = clampNumber(nudgeMsInput.value, motorSettings.nudgeMs, 60, 1000);
  motorSettings.swapDirections = swapDirectionsInput.checked;
  saveMotorSettings();
  syncWheelUi();
}

function syncWheelUi() {
  speedControl.value = motorSettings.speed;
  speedValue.textContent = String(motorSettings.speed);
  blueMsInput.value = motorSettings.blueMs;
  blueHomeMsInput.value = motorSettings.blueHomeMs;
  greenMsInput.value = motorSettings.greenMs;
  greenHomeMsInput.value = motorSettings.greenHomeMs;
  nudgeMsInput.value = motorSettings.nudgeMs;
  swapDirectionsInput.checked = motorSettings.swapDirections;
  wheelPosition.textContent = positionLabel(motorSettings.position);
    syncCalibrationUi();
  syncActionHighlights(state);
}

function syncActionHighlights(next = state) {
  const activeBin = manualActiveBin || activeBinForState(next);
  const activeStepSide = manualStepSide || activeStepSideForState(next);
  syncBinHighlights(activeBin);
  syncStepHighlights(activeStepSide);
  lidButton.classList.toggle("is-active-action", manualLidActive || activeActionForState(next));
}

function activeStepSideForState(next) {
  if (!next || next.status !== "running") {
    return "";
  }

  const steps = next.steps || [];
  const latest = steps[steps.length - 1] || {};
  const latestText = `${latest.title || ""} ${latest.value || ""} ${latest.detail || ""}`.toLowerCase();
  if (latestText.includes("left nudge") || latestText.includes("nudge left") || latestText.includes("left raw")) return "left";
  if (latestText.includes("right nudge") || latestText.includes("nudge right") || latestText.includes("right raw")) return "right";
  return "";
}

function activeActionForState(next) {
  if (!next || next.status !== "running") {
    return false;
  }

  const steps = next.steps || [];
  const latest = steps[steps.length - 1] || {};
  const latestText = `${latest.title || ""} ${latest.value || ""} ${latest.detail || ""}`.toLowerCase();
  return latestText.includes("opening lid") || latestText.includes("lid");
}

function syncBinHighlights(activeBin = "") {
  for (const [position, button] of Object.entries(binButtons)) {
    if (!button) continue;
    button.classList.toggle("is-current", !activeBin && motorSettings.position === position);
    button.classList.toggle("is-active-bin", activeBin === position);
  }
}

function activeBinForState(next) {
  if (!next || next.status !== "running") {
    return next?.status === "complete" ? "home" : "";
  }

  const steps = next.steps || [];
  const latest = steps[steps.length - 1] || {};
  const latestText = `${latest.title || ""} ${latest.value || ""} ${latest.detail || ""}`.toLowerCase();

  if (latestText.includes("returning home") || latestText.includes("garbage home") || latestText.includes("ready")) {
    return "home";
  }
  if (latestText.includes("blue left") || latestText.includes("recycle blue") || latestText.includes("recycle")) {
    return "blue";
  }
  if (latestText.includes("green right") || latestText.includes("organic green") || latestText.includes("organic")) {
    return "green";
  }
  return "";
}

async function moveWheelTo(targetPosition) {
  readWheelSettingsFromUi();

  try {
    const segments = buildWheelSegments(motorSettings.position, targetPosition);
    if (!segments.length) {
      motorSettings.position = targetPosition;
      saveMotorSettings();
      syncWheelUi();
      setWheelMessage("Already There", positionLabel(targetPosition));
      return;
    }

    await runWheelSequence(segments, targetPosition, `Moving ${positionLabel(targetPosition)}`);
  } catch (error) {
    setWheelError(error);
  }
}

function buildWheelSegments(fromPosition, toPosition) {
  if (fromPosition === toPosition) {
    return [];
  }

  if (!["home", "blue", "green"].includes(fromPosition)) {
    throw new Error("Position unknown. Nudge to Garbage, then Set Home.");
  }

  const blueNudges = motorSettings.blueMs;
  const blueHomeNudges = motorSettings.blueHomeMs;
  const greenNudges = motorSettings.greenMs;
  const greenHomeNudges = motorSettings.greenHomeMs;
  const nudgeMs = motorSettings.nudgeMs;
  const left = count => wheelStepSegments("left", count, nudgeMs);
  const right = count => wheelStepSegments("right", count, nudgeMs);
  const segmentMap = {
    home: {
      blue: left(blueNudges),
      green: right(greenNudges)
    },
    blue: {
      home: right(blueHomeNudges),
      green: [...right(blueHomeNudges), ...right(greenNudges)]
    },
    green: {
      home: left(greenHomeNudges),
      blue: [...left(greenHomeNudges), ...left(blueNudges)]
    }
  };

  return segmentMap[fromPosition]?.[toPosition] || [];
}
function wheelStepSegments(side, count, nudgeMs) {
  const normalized = Math.min(40, Math.max(0, Number.parseFloat(count)) || 0);
  const fullSteps = Math.floor(normalized);
  const fraction = normalized - fullSteps;
  const segments = Array.from({ length: fullSteps }, () => ({ side, ms: nudgeMs, fraction: 1 }));

  if (fraction >= 0.05) {
    segments.push({
      side,
      ms: Math.max(30, Math.round(nudgeMs * fraction)),
      fraction
    });
  }

  return segments;
}
async function nudgeWheel(side) {
  readWheelSettingsFromUi();
  const moved = await runWheelSequence(
    [{ side, ms: motorSettings.nudgeMs }],
    "manual",
    side === "left" ? "Nudge Left" : "Nudge Right"
  );

  if (moved) {
    recordCalibrationNudge(side);
  }
}

async function runWheelSequence(segments, finalPosition, label) {
  const runId = ++motorRunId;
  manualActiveBin = ["blue", "home", "green"].includes(finalPosition) ? finalPosition : "";
  setWheelBusy(true);
  syncActionHighlights(state);

  try {
    for (const segment of segments) {
      if (runId !== motorRunId) {
        return false;
      }
      await sendWheelPulse(segment, label);
      await sleep(150);
    }

    if (runId !== motorRunId) {
        return false;
      }

    motorSettings.position = finalPosition;
    saveMotorSettings();
    syncWheelUi();
    setWheelMessage("Wheel Stopped", positionLabel(finalPosition));
    return true;
  } catch (error) {
    motorSettings.position = "manual";
    saveMotorSettings();
    syncWheelUi();
    setWheelError(error);
    return false;
  } finally {
    if (runId === motorRunId) {
      manualActiveBin = "";
      setWheelBusy(false);
      syncActionHighlights(state);
    }
  }
}

async function sendWheelPulse(segment, label) {
  const ms = clampNumber(segment.ms, motorSettings.nudgeMs, 30, 1000);
  const direction = directionForSide(segment.side);
  const startedAt = Date.now();

  setWheelMessage(label, `${segment.side === "left" ? "Left" : "Right"} ${ms}ms raw step`);
  setStepButtonGlow(segment.side, true);

  try {
    const response = await fetch("/api/motor-raw", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ direction, ms })
  });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Wheel command failed");
    }

    await sleep(Math.max(0, ms - (Date.now() - startedAt)) + 300);
  } finally {
    setStepButtonGlow(segment.side, false);
  }
}

async function rawWheel(side) {
  readWheelSettingsFromUi();
  const ms = clampNumber(motorSettings.nudgeMs, 60, 60, 1000);
  const sideLabel = side === "left" ? "left" : "right";
  if (!window.confirm(`Run full power ${sideLabel} for ${ms} ms? Keep hands clear.`)) {
    return;
  }

  setWheelBusy(true);
  try {
    setStepButtonGlow(side, true);
    setWheelMessage(side === "left" ? "Full Left" : "Full Right", `${ms}ms`);
    const response = await fetch("/api/motor-raw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction: directionForSide(side), ms })
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Raw wheel command failed");
    }

    motorSettings.position = "manual";
    saveMotorSettings();
    syncWheelUi();
    if (calibrationState.target) {
      recordCalibrationNudge(side);
    } else {
      setWheelMessage("Raw Stopped", "Position unknown");
    }
  } catch (error) {
    setWheelError(error);
  } finally {
    setStepButtonGlow(side, false);
    setWheelBusy(false);
  }
}

async function stopWheel() {
  ++motorRunId;
  setWheelBusy(true);

  try {
    const response = await fetch("/api/motor-raw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction: "stop", ms: 50 })
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Wheel stop failed");
    }

    motorSettings.position = "manual";
    saveMotorSettings();
    syncWheelUi();
    setWheelMessage("Wheel Stopped", "Position unknown");
  } catch (error) {
    setWheelError(error);
  } finally {
    setWheelBusy(false);
  }
}

function setStepButtonGlow(side, isActive) {
  manualStepSide = isActive ? side : "";
  syncStepHighlights(manualStepSide || activeStepSideForState(state));
}

function syncStepHighlights(side = "") {
  for (const button of [nudgeLeftButton, rawLeftButton]) {
    button?.classList.toggle("is-step-active", side === "left");
  }
  for (const button of [nudgeRightButton, rawRightButton]) {
    button?.classList.toggle("is-step-active", side === "right");
  }
}

function directionForSide(side) {
  const leftDirection = motorSettings.swapDirections ? "forward" : "reverse";
  const rightDirection = motorSettings.swapDirections ? "reverse" : "forward";
  return side === "left" ? leftDirection : rightDirection;
}

function startCalibration(target) {
  calibrationState.target = target;
  calibrationState.left = 0;
  calibrationState.right = 0;
    syncCalibrationUi();
  setWheelMessage(`Count ${positionLabel(target)}`, "L0 R0");
}

function resetCalibration() {
  calibrationState.target = "";
  calibrationState.left = 0;
  calibrationState.right = 0;
    syncCalibrationUi();
  setWheelMessage("Count Reset", positionLabel(motorSettings.position));
}

function recordCalibrationNudge(side) {
  if (!calibrationState.target) {
    return;
  }

  if (side === "left") {
    calibrationState.left += 1;
  } else {
    calibrationState.right += 1;
  }

    syncCalibrationUi();
  setWheelMessage(
    `Count ${positionLabel(calibrationState.target)}`,
    `L${calibrationState.left} R${calibrationState.right}`
  );
}

function saveCalibrationCount() {
  if (!calibrationState.target) {
    setWheelError(new Error("Choose Blue or Green count first"));
    return;
  }

  const count = calibrationNetCountForTarget(calibrationState.target);
  if (count < 1) {
    setWheelError(new Error("No nudge count to save"));
    return;
  }

  if (calibrationState.target === "blue") {
    motorSettings.blueMs = count;
  } else {
    motorSettings.greenMs = count;
  }

  motorSettings.position = calibrationState.target;
  saveMotorSettings();
  syncWheelUi();
  setWheelMessage(`Saved ${positionLabel(calibrationState.target)}`, `${count} nudges`);
}

function calibrationNetCountForTarget(target) {
  const primary = target === "blue" ? calibrationState.left : calibrationState.right;
  const opposing = target === "blue" ? calibrationState.right : calibrationState.left;
  return Math.max(0, primary - opposing);
}

function syncCalibrationUi() {
  if (!calibrationTarget) {
    return;
  }

  const targetLabel = calibrationState.target ? `${positionLabel(calibrationState.target)} Count` : "Idle";
  const netCount = calibrationState.target ? calibrationNetCountForTarget(calibrationState.target) : 0;
  calibrationTarget.textContent = targetLabel;
  calibrationLeftCount.textContent = String(calibrationState.left);
  calibrationRightCount.textContent = String(calibrationState.right);
  calibrationNetCount.textContent = String(netCount);
  calibrationSaveButton.textContent = calibrationState.target === "blue"
    ? "Save Blue"
    : calibrationState.target === "green"
      ? "Save Green"
      : "Save Count";
  calibrationSaveButton.disabled = motorBusy || !calibrationState.target || netCount < 1;
  calibrationStartBlueButton.disabled = motorBusy;
  calibrationStartGreenButton.disabled = motorBusy;
  calibrationResetButton.disabled = motorBusy || (!calibrationState.target && calibrationState.left === 0 && calibrationState.right === 0);
}
function setWheelBusy(isBusy) {
  motorBusy = isBusy;
  for (const button of [blueBinButton, homeBinButton, greenBinButton, nudgeLeftButton, nudgeRightButton, rawLeftButton, rawRightButton, setHomeButton]) {
    button.disabled = isBusy;
  }
  stopWheelButton.disabled = !isBusy;
  lidButton.disabled = isBusy;
  runButton.disabled = state.status === "running" || !cameraReady || motorBusy;
    syncCalibrationUi();
}

function setWheelMessage(line1, line2) {
  systemStatus.textContent = line1;
  lcdLine1.textContent = lcdText(line1);
  lcdLine2.textContent = lcdText(line2);
}

function setWheelError(error) {
  systemStatus.textContent = "Wheel Error";
  lcdLine1.textContent = lcdText("Wheel failed");
  lcdLine2.textContent = lcdText(error.message);
}

function positionLabel(position) {
  if (position === "blue") return "Blue Left";
  if (position === "green") return "Green Right";
  if (position === "manual") return "Manual";
  return "Garbage Home";
}

function clampNumber(value, fallback, min, max) {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, parsed));
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
lidButton.addEventListener("click", async () => {
  lidButton.disabled = true;
  manualLidActive = true;
  syncActionHighlights(state);
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
      manualLidActive = false;
      syncActionHighlights(state);
    }, 1800);
    lidButton.disabled = false;
  }
});

async function classifyImageDataUrl(imageDataUrl) {
  showCapturePreview(imageDataUrl);

  const response = await fetch("/api/classify-upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ imageDataUrl, sorterSettings: sorterSettingsPayload() })
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || "Classification failed");
  }

  motorSettings.position = "home";
  saveMotorSettings();
  syncWheelUi();
  chartLoaded = false;
  loadClassificationSummary({ force: true });
}

function sorterSettingsPayload() {
  readWheelSettingsFromUi();
  return {
    blueNudges: motorSettings.blueMs,
    blueHomeNudges: motorSettings.blueHomeMs,
    greenNudges: motorSettings.greenMs,
    greenHomeNudges: motorSettings.greenHomeMs,
    nudgeMs: motorSettings.nudgeMs,
    speed: motorSettings.speed,
    leftDirection: directionForSide("left"),
    rightDirection: directionForSide("right")
  };
}
function readImageFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Could not read uploaded image"));
    reader.readAsDataURL(file);
  });
}

function showRunError(error) {
  state = {
    ...state,
    status: "error",
    message: error.message,
    steps: [...state.steps, { title: "Error", value: "Stopped", detail: error.message }]
  };
  render(state);
}
async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    const config = await response.json();
    cameraUrl.textContent = config.cameraLabel || "Laptop HD Camera";
    if (aiModeLabel) aiModeLabel.textContent = config.aiMode || "Gemini + Edge Impulse";
  } catch (_error) {
    cameraUrl.textContent = "Laptop HD Camera";
    if (aiModeLabel) aiModeLabel.textContent = "Gemini + Edge Impulse";
  }
}

async function getCameraPermissionState() {
  if (!navigator.permissions?.query) {
    return "unknown";
  }

  try {
    const status = await Promise.race([
      navigator.permissions.query({ name: "camera" }),
      new Promise(resolve => setTimeout(() => resolve(null), 800))
    ]);
    return status?.state || "unknown";
  } catch (_error) {
    return "unknown";
  }
}

function withTimeout(promise, ms, message) {
  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => reject(namedCameraError("Timeout", message)), ms);
    promise.then(
      result => {
        clearTimeout(timeoutId);
        resolve(result);
      },
      error => {
        clearTimeout(timeoutId);
        reject(error);
      }
    );
  });
}

async function startLaptopCamera() {
  stopCameraStream();
  cameraReady = false;
  cameraError = "";
  runButton.disabled = true;
  cameraRetryButton.disabled = true;
  cameraSelect.disabled = true;
  capturePreview.hidden = true;
  imageStage.classList.remove("has-preview");
  setCameraPlaceholder("Starting camera", "Requesting browser camera access");
  cameraUrl.textContent = "Browser camera";
  systemStatus.textContent = "Camera Starting";

  try {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw namedCameraError("Unsupported", "Browser camera access is not available");
    }

    const permissionState = await getCameraPermissionState();
    if (permissionState === "denied") {
      throw namedCameraError("NotAllowedError", "Camera access is blocked in Chrome. Open site settings and allow camera.");
    }

    const videoDevices = await refreshCameraDevices({ silent: true });

    const stream = await withTimeout(
      openCameraStream(videoDevices),
      12000,
      "Camera permission or device selection timed out. Click Retry and allow camera access."
    );

    currentStream = stream;
    cameraVideo.srcObject = stream;
    cameraVideo.hidden = false;
    cameraPlaceholder.hidden = true;

    const playPromise = cameraVideo.play();
    if (playPromise) {
      await withTimeout(
        playPromise,
        3500,
        "Camera opened, but Chrome did not start video playback. Click Retry or refresh the page."
      );
    }
    await waitForVideoMetadata();

    const activeDeviceId = stream.getVideoTracks()[0]?.getSettings?.().deviceId;
    if (activeDeviceId) {
      selectedDeviceId = activeDeviceId;
    }

    cameraReady = true;
    cameraError = "";
    await refreshCameraDevices();
    render(state);
  } catch (error) {
    cameraReady = false;
    cameraError = formatCameraError(error);
    cameraVideo.hidden = true;
    cameraPlaceholder.hidden = false;
    setCameraPlaceholder("Camera unavailable", cameraError);
    cameraUrl.textContent = "Browser camera unavailable";
    await refreshCameraDevices();
    render(state);
  } finally {
    cameraRetryButton.disabled = false;
    cameraSelect.disabled = cameraSelect.options.length <= 1;
  }
}
async function openCameraStream(videoDevices = []) {
  const attempts = [];
  const seen = new Set();

  function addAttempt(label, video) {
    const key = JSON.stringify(video);
    if (seen.has(key)) return;
    seen.add(key);
    attempts.push({ label, constraints: { video, audio: false } });
  }

  if (selectedDeviceId) {
    addAttempt("selected camera", { deviceId: { exact: selectedDeviceId } });
  }

  for (const device of videoDevices) {
    if (device.deviceId) {
      addAttempt(device.label || "detected camera", { deviceId: { exact: device.deviceId } });
    }
  }

  addAttempt("HD camera", { width: { ideal: 1280 }, height: { ideal: 720 } });
  addAttempt("default camera", true);

  let lastError = null;
  for (const attempt of attempts) {
    try {
      setCameraPlaceholder("Starting camera", `Opening ${attempt.label}`);
      return await navigator.mediaDevices.getUserMedia(attempt.constraints);
    } catch (error) {
      lastError = error;
      if (isPermissionError(error)) {
        throw error;
      }
      if (attempt.label === "selected camera") {
        selectedDeviceId = "";
      }
      await sleep(250);
    }
  }

  throw lastError || namedCameraError("NotFoundError", "No camera detected by Chrome");
}

async function refreshCameraDevices(options = {}) {
  if (!navigator.mediaDevices?.enumerateDevices) {
    cameraSelect.innerHTML = '<option value="">Auto-detect camera</option><option value="" disabled>No device list</option>';
    cameraUrl.textContent = "Camera list unavailable";
    return [];
  }

  try {
    const devices = await withTimeout(navigator.mediaDevices.enumerateDevices(), 2500, "Camera device list timed out");
    const videoDevices = devices.filter(device => device.kind === "videoinput");
    const autoDetectOption = `<option value=""${selectedDeviceId ? "" : " selected"}>Auto-detect camera</option>`;

    if (!videoDevices.length) {
      cameraSelect.innerHTML = `${autoDetectOption}<option value="" disabled>No camera detected yet</option>`;
      cameraSelect.value = "";
      cameraUrl.textContent = "Auto-detect camera";
      return [];
    }

    if (selectedDeviceId && !videoDevices.some(device => device.deviceId === selectedDeviceId)) {
      selectedDeviceId = "";
    }

    const optionsHtml = [
      autoDetectOption,
      ...videoDevices.map((device, index) => {
        const label = device.label || `Camera ${index + 1}`;
        const selected = device.deviceId === selectedDeviceId ? " selected" : "";
        return `<option value="${escapeHtml(device.deviceId)}"${selected}>${escapeHtml(label)}</option>`;
      })
    ];

    cameraSelect.innerHTML = optionsHtml.join("");
    cameraSelect.value = selectedDeviceId;
    const selectedDevice = selectedDeviceId
      ? videoDevices.find(device => device.deviceId === selectedDeviceId)
      : null;
    cameraUrl.textContent = selectedDevice?.label || `Auto-detect camera (${videoDevices.length} found)`;
    return videoDevices;
  } catch (error) {
    cameraSelect.innerHTML = '<option value="">Auto-detect camera</option><option value="" disabled>Camera list blocked</option>';
    cameraSelect.value = "";
    cameraUrl.textContent = "Camera list blocked";
    if (!cameraError && !options.silent) {
      cameraError = formatCameraError(error);
    }
    return [];
  }
}

function waitForVideoMetadata() {
  return new Promise((resolve, reject) => {
    if (cameraVideo.videoWidth && cameraVideo.videoHeight) {
      resolve();
      return;
    }

    const timeout = setTimeout(() => {
      cameraVideo.removeEventListener("loadedmetadata", onLoaded);
      reject(namedCameraError("Timeout", "Camera did not provide a video frame"));
    }, 8000);

    function onLoaded() {
      clearTimeout(timeout);
      resolve();
    }

    cameraVideo.addEventListener("loadedmetadata", onLoaded, { once: true });
  });
}

function captureCurrentFrame() {
  if (!cameraReady) {
    throw new Error(cameraError || "Laptop camera is not ready");
  }

  const sourceWidth = cameraVideo.videoWidth;
  const sourceHeight = cameraVideo.videoHeight;
  if (!sourceWidth || !sourceHeight) {
    throw new Error("Laptop camera has no video frame yet");
  }

  const maxWidth = 768;
  const scale = Math.min(1, maxWidth / sourceWidth);
  const targetWidth = Math.round(sourceWidth * scale);
  const targetHeight = Math.round(sourceHeight * scale);

  captureCanvas.width = targetWidth;
  captureCanvas.height = targetHeight;

  const context = captureCanvas.getContext("2d");
  context.drawImage(cameraVideo, 0, 0, targetWidth, targetHeight);
  return captureCanvas.toDataURL("image/jpeg", 0.74);
}

function showCapturePreview(imageDataUrl) {
  capturePreview.src = imageDataUrl;
  capturePreview.hidden = false;
  imageStage.classList.add("has-preview");
}

function stopCameraStream() {
  if (currentStream) {
    for (const track of currentStream.getTracks()) {
      track.stop();
    }
  }
  currentStream = null;
  cameraVideo.srcObject = null;
}

function setCameraPlaceholder(message, detail) {
  cameraMessage.textContent = message;
  cameraDetail.textContent = detail || "";
}

function namedCameraError(name, message) {
  const error = new Error(message);
  error.name = name;
  return error;
}

function isPermissionError(error) {
  return ["NotAllowedError", "SecurityError", "PermissionDeniedError"].includes(error?.name);
}

function formatCameraError(error) {
  const name = error?.name || "CameraError";
  const message = error?.message || String(error);
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return "Permission blocked. Allow camera access for localhost in Chrome.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No camera detected by Chrome. Check USB connection and Windows camera privacy.";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "Camera is busy or blocked by another app. Close Camera/Teams/Zoom and retry.";
  }
  if (name === "OverconstrainedError" || name === "ConstraintNotSatisfiedError") {
    return "Camera rejected the requested video format. Choose another camera or retry.";
  }
  if (name === "SecurityError") {
    return "Browser blocked camera access for this page.";
  }
  return `${name}: ${message}`;
}

function connectEvents() {
  const events = new EventSource("/api/events");
  events.addEventListener("snapshot", event => {
    state = JSON.parse(event.data);
    render(state);
  });
}

function render(next) {
  systemStatus.textContent = !cameraReady && cameraError && next.status !== "running"
    ? "Camera Error"
    : statusLabel(next.status);
  runButton.disabled = next.status === "running" || !cameraReady || motorBusy;

  if (next.imageDataUrl) {
    showCapturePreview(next.imageDataUrl);
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
  flowList.innerHTML = steps.map((step, index) => {
    const isLatest = index === steps.length - 1;
    const itemClass = [
      "flow-item",
      isLatest ? "current" : "complete",
      next.status === "error" && isLatest ? "error" : ""
    ].filter(Boolean).join(" ");
    const title = step.title || statusLabel(next.status);
    const value = step.value || "";
    const detail = step.detail || next.message || "";
    const time = step.at
      ? new Date(step.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })
      : "";
    const tooltip = [title, value, detail, time].filter(Boolean).join(" | ");

    return `<li class="${itemClass}" title="${escapeHtml(tooltip)}"><strong>${escapeHtml(title)}</strong></li>`;
  }).join("");
  syncActionHighlights(next);
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
