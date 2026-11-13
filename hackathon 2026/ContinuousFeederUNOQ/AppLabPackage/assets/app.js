const uiElements = {
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

const webui = new WebUI();
const POLL_INTERVAL_MS = 1000;
const SPEED_DEBOUNCE_MS = 90;
let speedTimer;
let editingSpeed = false;

function setConnection(state, text) {
  uiElements.connection.className = `connection ${state}`;
  uiElements.connectionText.textContent = text;
}

function render(status) {
  setConnection(
    status.wifiConnected ? 'online' : 'error',
    status.wifiConnected ? 'Wi-Fi Connected' : 'Wi-Fi Disconnected'
  );

  uiElements.motorStatus.textContent = status.state;
  uiElements.motorStatus.className = `status-value ${
    status.emergencyStopped ? 'emergency' : status.running ? 'running' : ''
  }`;
  uiElements.percentValue.textContent = status.percent;
  uiElements.pwmValue.textContent = status.pwm;

  if (!editingSpeed) {
    uiElements.speedSlider.value = status.percent;
    uiElements.speedOutput.value = `${status.percent}%`;
  }

  uiElements.startButton.disabled = status.running || status.emergencyStopped;
  uiElements.stopButton.disabled = !status.running || status.emergencyStopped;
  uiElements.resetButton.hidden = !status.emergencyStopped;
  uiElements.emergencyButton.disabled = status.emergencyStopped;

  if (status.error) {
    showNotice(status.error, true);
  } else if (status.message && status.message !== 'Speed updated') {
    showNotice(status.message);
  }
}

function showNotice(message = '', isError = false) {
  uiElements.notice.textContent = message;
  uiElements.notice.className = `notice${isError ? ' error' : ''}`;
}

function sendCommand(name, data) {
  webui.send_message(name, data);
}

webui.on_connect(() => {
  setConnection('online', 'Connected');
  showNotice('');
  sendCommand('get_status');
});

webui.on_disconnect(() => {
  setConnection('error', 'Connection Lost');
  showNotice('Connection to the UNO Q was lost.', true);
});

webui.on_message('feeder_status', render);
webui.on_message('feeder_error', (error) => {
  showNotice(error.message || 'Motor controller is unavailable.', true);
});

uiElements.speedSlider.addEventListener('input', () => {
  editingSpeed = true;
  const percent = Number(uiElements.speedSlider.value);
  uiElements.speedOutput.value = `${percent}%`;
  uiElements.percentValue.textContent = percent;
  clearTimeout(speedTimer);
  speedTimer = setTimeout(() => {
    sendCommand('set_speed', { percent });
    editingSpeed = false;
  }, SPEED_DEBOUNCE_MS);
});

uiElements.startButton.addEventListener('click', () => sendCommand('start_feeder'));
uiElements.stopButton.addEventListener('click', () => sendCommand('stop_feeder'));
uiElements.emergencyButton.addEventListener('click', () => sendCommand('emergency_stop'));
uiElements.resetButton.addEventListener('click', () => sendCommand('reset_emergency_stop'));

setInterval(() => sendCommand('get_status'), POLL_INTERVAL_MS);
