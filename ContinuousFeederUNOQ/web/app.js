const ui = {
  connection: document.querySelector('#connection'),
  connectionText: document.querySelector('#connectionText'),
  motorStatus: document.querySelector('#motorStatus'),
  percentValue: document.querySelector('#percentValue'),
  pwmValue: document.querySelector('#pwmValue'),
  speedSlider: document.querySelector('#speedSlider'),
  speedOutput: document.querySelector('#speedOutput'),
  startButton: document.querySelector('#startButton'),
  stopButton: document.querySelector('#stopButton'),
  emergencyButton: document.querySelector('#emergencyButton'),
  resetButton: document.querySelector('#resetButton'),
  notice: document.querySelector('#notice')
};

const POLL_INTERVAL_MS = 1000;
const REQUEST_TIMEOUT_MS = 1500;
const SPEED_DEBOUNCE_MS = 90;
let speedTimer;
let pollInFlight = false;
let editingSpeed = false;

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(path, { cache: 'no-store', signal: controller.signal, ...options });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || `Request failed (${response.status})`);
    setConnection(true);
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

function setConnection(online, failed = false) {
  ui.connection.className = `connection ${online ? 'online' : failed ? 'error' : 'offline'}`;
  ui.connectionText.textContent = online ? 'Connected' : failed ? 'Connection lost' : 'Connecting';
}

function render(status) {
  if (status.simulationMode) {
    ui.connection.className = 'connection online';
    ui.connectionText.textContent = 'Simulation';
  } else if (status.wifiConnected) {
    ui.connection.className = 'connection online';
    ui.connectionText.textContent = 'Wi-Fi Connected';
  } else {
    ui.connection.className = 'connection error';
    ui.connectionText.textContent = 'Wi-Fi Disconnected';
  }
  ui.motorStatus.textContent = status.state;
  ui.motorStatus.className = `status-value ${status.emergencyStopped ? 'emergency' : status.running ? 'running' : ''}`;
  ui.percentValue.textContent = status.percent;
  ui.pwmValue.textContent = status.pwm;

  if (!editingSpeed) {
    ui.speedSlider.value = status.percent;
    ui.speedOutput.value = `${status.percent}%`;
  }

  ui.startButton.disabled = status.running || status.emergencyStopped;
  ui.stopButton.disabled = !status.running || status.emergencyStopped;
  ui.resetButton.hidden = !status.emergencyStopped;
  ui.emergencyButton.disabled = status.emergencyStopped;
}

function showNotice(message = '', isError = false) {
  ui.notice.textContent = message;
  ui.notice.className = `notice${isError ? ' error' : ''}`;
}

async function command(path) {
  setControlsBusy(true);
  try {
    const status = await api(path, { method: 'POST' });
    render(status);
    showNotice(status.message);
  } catch (error) {
    setConnection(false, true);
    showNotice(error.name === 'AbortError' ? 'Controller did not respond in time.' : error.message, true);
  } finally {
    setControlsBusy(false);
  }
}

function setControlsBusy(busy) {
  ui.startButton.disabled = busy || ui.startButton.disabled;
  ui.stopButton.disabled = busy || ui.stopButton.disabled;
  ui.resetButton.disabled = busy;
}

async function updateSpeed(percent) {
  try {
    const status = await api(`/api/speed?percent=${encodeURIComponent(percent)}`, { method: 'POST' });
    render(status);
    showNotice('');
  } catch (error) {
    setConnection(false, true);
    showNotice('Speed update failed. The displayed setting may be stale.', true);
  } finally {
    editingSpeed = false;
  }
}

async function pollStatus() {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    render(await api('/api/status'));
  } catch (_) {
    setConnection(false, true);
  } finally {
    pollInFlight = false;
  }
}

ui.speedSlider.addEventListener('input', () => {
  editingSpeed = true;
  const percent = ui.speedSlider.value;
  ui.speedOutput.value = `${percent}%`;
  ui.percentValue.textContent = percent;
  clearTimeout(speedTimer);
  speedTimer = setTimeout(() => updateSpeed(percent), SPEED_DEBOUNCE_MS);
});

ui.startButton.addEventListener('click', () => command('/api/start'));
ui.stopButton.addEventListener('click', () => command('/api/stop'));
ui.emergencyButton.addEventListener('click', () => command('/api/emergency-stop'));
ui.resetButton.addEventListener('click', () => command('/api/reset-estop'));

pollStatus();
setInterval(pollStatus, POLL_INTERVAL_MS);
