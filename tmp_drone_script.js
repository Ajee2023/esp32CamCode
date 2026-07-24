
    let map = L.map('map').setView([43.7315, -79.7624], 15);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);

  const droneIcon = L.divIcon({
    className: '',
    html: `
      <div class="drone-marker" aria-label="Drone marker">
        <svg viewBox="0 0 128 128" role="img" aria-hidden="true">
          <circle cx="32" cy="32" r="20" fill="#2d7dd2"/>
          <circle cx="96" cy="32" r="20" fill="#2d7dd2"/>
          <circle cx="32" cy="96" r="20" fill="#2d7dd2"/>
          <circle cx="96" cy="96" r="20" fill="#2d7dd2"/>
          <circle cx="32" cy="32" r="12" fill="#3b4658"/>
          <circle cx="96" cy="32" r="12" fill="#3b4658"/>
          <circle cx="32" cy="96" r="12" fill="#3b4658"/>
          <circle cx="96" cy="96" r="12" fill="#3b4658"/>
          <path d="M42 36c8 10 18 14 22 14s14-4 22-14c6 7 7 17 2 27-5 10-13 17-24 19v10h-8V82c-11-2-19-9-24-19-5-10-4-20 2-27z" fill="#3b4658"/>
          <circle cx="64" cy="64" r="10" fill="#ffffff"/>
        </svg>
      </div>
    `,
    iconSize: [38, 38],
    iconAnchor: [19, 19],
    popupAnchor: [0, -20]
  });
  let droneMarker = L.marker([43.7315, -79.7624], { title: 'Drone', icon: droneIcon }).addTo(map);
  droneMarker.bindTooltip('DRONE-001', { direction: 'top', offset: [0, -18] });
  droneMarker.bindPopup('Loading drone details...');
  const targetIcon = L.divIcon({ className: '', html: '<div class="target-pin"></div>', iconSize: [20, 20], iconAnchor: [10, 20], popupAnchor: [0, -20] });
  let targetMarker = null;
  let routeLine = null;
  let zoneRectangle = null;
  let noFlyZoneLayers = new Map();
  let pendingZoneCorner = null;
  let pendingNoFlyPoints = [];
  let pendingNoFlyPreview = null;
  let navigationStartTime = 0;
  let navigationStartDistance = 0;
  let lastFlightStatus = 'idle';
  let weatherPushTimer = null;
  let speedMultiplier = 1;
  let activeMapMode = null;
  let activeNoFlyMode = null;
  let selectedZone = null;
  let noFlyZones = [];
  let binMarkers = new Map();
  let suppressStatusPollingError = false;
  let celebrationTimer = null;
  let celebrationAudioContext = null;
  let celebrationSoundTimer = null;
  let mapAlertTimer = null;
  let pendingNoFlySaveRequest = null;
  let selectedDispatchEntryIds = new Set();
  let latestTruckDispatch = { batches: [], open_item_count: 0 };
  let latestBinsSnapshot = [];

  const binStyles = {
    green: { color: '#2a9d8f', accent: '#7fdad0', label: 'Green Bin' },
    blue: { color: '#2d6cdf', accent: '#bcd4ff', label: 'Blue Bin' },
    garbage: { color: '#656d76', accent: '#d7dce1', label: 'Black Bin' }
  };

  function showTopTab(tabName) {
    const isFlight = tabName === 'flight';
    const isSummary = tabName === 'summary';
    const isTruck = tabName === 'truck';
    document.getElementById('tabFlightButton').classList.toggle('active', isFlight);
    document.getElementById('tabSummaryButton').classList.toggle('active', isSummary);
    document.getElementById('tabTruckButton').classList.toggle('active', isTruck);
    document.getElementById('flightTabPanel').classList.toggle('active', isFlight);
    document.getElementById('dispatchTabPanel').classList.toggle('active', isSummary);
    document.getElementById('truckTabPanel').classList.toggle('active', isTruck);
    if (isFlight) {
      setTimeout(() => map.invalidateSize(), 60);
      setTimeout(() => map.invalidateSize(), 180);
    }
  }

  window.addEventListener('load', () => {
    setTimeout(() => map.invalidateSize(), 80);
    setTimeout(() => map.invalidateSize(), 220);
  });

  function setMessage(message, isError = false) {
    const messageBox = document.getElementById('messageBox');
    messageBox.innerText = message || 'No message';
    messageBox.className = isError ? 'message-box error' : 'message-box';
  }

  function showMapAlert(message, type = 'error', title = '') {
    const mapAlert = document.getElementById('mapAlert');
    if (!mapAlert || !message) return;
    const normalizedType = ['error', 'warning', 'success'].includes(type) ? type : 'error';
    const defaultTitle = normalizedType === 'error'
      ? 'Flight Alert'
      : normalizedType === 'warning'
        ? 'Route Caution'
        : 'Mission Update';
    mapAlert.className = `map-alert ${normalizedType} visible`;
    mapAlert.innerHTML = `<div class="map-alert-title">${title || defaultTitle}</div><div>${message}</div>`;
    if (mapAlertTimer) clearTimeout(mapAlertTimer);
    mapAlertTimer = setTimeout(() => {
      mapAlert.className = `map-alert ${normalizedType}`;
    }, normalizedType === 'error' ? 5200 : 3600);
  }

  function updateDroneMarkerDetails(positionData) {
    const drone = positionData?.drone || {};
    const statusLabel = drone.status_label || 'IDLE';
    const assignmentId = drone.assignment_id || 'None';
    const batteryText = Number.isFinite(drone.battery_level) ? `${drone.battery_level.toFixed(1)}%` : 'Not available';
    const latText = Number(positionData?.lat ?? 0).toFixed(5);
    const lonText = Number(positionData?.lon ?? 0).toFixed(5);
    const droneId = drone.id || 'DRONE-001';
    const tooltipText = `${droneId} • ${statusLabel}`;
    const popupContent = `
      <div style="min-width:220px;">
        <div style="font-weight:700;color:#1d3557;margin-bottom:6px;">${droneId}</div>
        <div style="font-size:12px;color:#5d6b86;margin-bottom:3px;">Status: ${statusLabel}</div>
        <div style="font-size:12px;color:#5d6b86;margin-bottom:3px;">Latitude: ${latText}</div>
        <div style="font-size:12px;color:#5d6b86;margin-bottom:3px;">Longitude: ${lonText}</div>
        <div style="font-size:12px;color:#5d6b86;margin-bottom:3px;">Battery: ${batteryText}</div>
        <div style="font-size:12px;color:#5d6b86;margin-bottom:0;">Assignment: ${assignmentId}</div>
      </div>
    `;
    droneMarker.setTooltipContent(tooltipText);
    droneMarker.setPopupContent(popupContent);
    droneMarker.options.title = tooltipText;
    if (droneMarker._icon) {
      droneMarker._icon.setAttribute('role', 'img');
      droneMarker._icon.setAttribute('aria-label', tooltipText);
      droneMarker._icon.setAttribute('title', tooltipText);
    }
  }

  function getNoFlyModeButtonMap() {
    return {
      polygon: 'modeNoFlyPolygon'
    };
  }

  function resetNoFlyModeButtons() {
    Object.values(getNoFlyModeButtonMap()).forEach(id => {
      const element = document.getElementById(id);
      if (element) element.classList.remove('active');
    });
  }

  function clearNoFlyDrawing() {
    pendingNoFlyPoints = [];
    if (pendingNoFlyPreview) {
      map.removeLayer(pendingNoFlyPreview);
      pendingNoFlyPreview = null;
    }
    document.getElementById('noFlyHint').innerText = 'Draft cleared. Saved no-fly zones remain active until you disable or delete them.';
  }

  function setNoFlyMode(mode) {
    activeMapMode = null;
    activeNoFlyMode = mode;
    pendingZoneCorner = null;
    clearNoFlyDrawing();
    document.querySelectorAll('.mode-button').forEach(button => button.classList.remove('active'));
    resetNoFlyModeButtons();
    const buttonId = getNoFlyModeButtonMap()[mode];
    if (buttonId) document.getElementById(buttonId).classList.add('active');
    const hints = {
      polygon: 'Click multiple points around the restricted area, then use Finish Polygon.'
    };
    document.getElementById('noFlyHint').innerText = hints[mode] || 'Draw a no-fly zone on the map.';
    document.getElementById('toolHint').innerText = 'No-fly-zone drawing is active.';
  }

  function createNoFlyLayer(zone) {
    const active = zone.is_active;
    const style = {
      color: active ? '#ef476f' : '#9aa7bd',
      weight: 2,
      fillColor: active ? '#ef476f' : '#c7d0df',
      fillOpacity: active ? 0.14 : 0.06,
      dashArray: active ? null : '6 6'
    };
    if (zone.shape_type === 'rectangle') {
      const geometry = zone.geometry;
      return L.rectangle([[geometry.south, geometry.west], [geometry.north, geometry.east]], style);
    }
    if (zone.shape_type === 'circle') {
      return L.circle([zone.geometry.center.lat, zone.geometry.center.lon], { ...style, radius: zone.geometry.radius_meters });
    }
    return L.polygon((zone.geometry.points || []).map(point => [point.lat, point.lon]), style);
  }

  function renderNoFlyZones(zones) {
    noFlyZones = Array.isArray(zones) ? zones : [];
    const seenIds = new Set();
    noFlyZones.forEach(zone => {
      seenIds.add(zone.id);
      const popup = `<div style="min-width:180px;"><div style="font-weight:700;color:#1d3557;margin-bottom:4px;">${zone.name}</div><div style="font-size:12px;color:#5d6b86;">${zone.description || 'Restricted airspace'}</div><div style="font-size:12px;color:#5d6b86;margin-top:4px;">${zone.shape_type} • buffer ${Math.round(zone.buffer_distance_meters || 0)}m • ${zone.is_active ? 'Active' : 'Inactive'}</div></div>`;
      if (noFlyZoneLayers.has(zone.id)) {
        const layer = noFlyZoneLayers.get(zone.id);
        map.removeLayer(layer);
      }
      const layer = createNoFlyLayer(zone).addTo(map);
      layer.bindPopup(popup);
      noFlyZoneLayers.set(zone.id, layer);
    });
    Array.from(noFlyZoneLayers.keys()).forEach(id => {
      if (!seenIds.has(id)) {
        map.removeLayer(noFlyZoneLayers.get(id));
        noFlyZoneLayers.delete(id);
      }
    });

    const list = document.getElementById('noFlyZoneList');
    list.innerHTML = '';
    if (!noFlyZones.length) {
      list.innerHTML = '<div class="zone-item"><div class="zone-item-copy">No saved no-fly zones are active or stored.</div></div>';
      return;
    }
    noFlyZones.forEach(zone => {
      const item = document.createElement('div');
      item.className = `zone-item ${zone.is_active ? '' : 'inactive'}`.trim();
      item.innerHTML = `
        <div class="zone-item-title">
          <span>${zone.name}</span>
          <span>${zone.is_active ? 'Active' : 'Inactive'}</span>
        </div>
        <div class="zone-item-copy">${zone.shape_type} zone • buffer ${Math.round(zone.buffer_distance_meters || 0)}m</div>
        <div class="zone-item-copy">${zone.description || 'Restricted airspace'}</div>
        <div class="zone-item-actions">
          <button type="button" class="zone-action-button zone-action-toggle" onclick="toggleNoFlyZoneActive(${zone.id}, ${zone.is_active ? 'false' : 'true'})">${zone.is_active ? 'Disable' : 'Enable'}</button>
          <button type="button" class="zone-action-button zone-action-edit" onclick="editNoFlyZone(${zone.id})">Edit</button>
          <button type="button" class="zone-action-button zone-action-delete" onclick="deleteNoFlyZone(${zone.id})">Delete</button>
        </div>
      `;
      list.appendChild(item);
    });
  }

  function openNoFlyZoneModal(shapeType, geometry, defaults = {}) {
    pendingNoFlySaveRequest = { shapeType, geometry };
    document.getElementById('noFlyZoneName').value = defaults.name || 'Restricted Airspace';
    document.getElementById('noFlyZoneDescription').value = defaults.description || 'Restricted airspace';
    document.getElementById('noFlyZoneBuffer').value = String(Math.round(defaults.buffer_distance_meters ?? 20));
    const modal = document.getElementById('noFlyZoneModal');
    modal.classList.add('visible');
    modal.setAttribute('aria-hidden', 'false');
    setTimeout(() => document.getElementById('noFlyZoneName').focus(), 0);
  }

  function promptNoFlyZoneDetails(defaults = {}) {
    const name = window.prompt('No-fly-zone name:', defaults.name || 'Restricted Airspace');
    if (!name) return null;
    const description = window.prompt('No-fly-zone description:', defaults.description || 'Restricted airspace');
    if (description === null) return null;
    const buffer = window.prompt('Caution buffer in meters:', String(Math.round(defaults.buffer_distance_meters ?? 20)));
    if (buffer === null) return null;
    const parsedBuffer = Number(buffer);
    if (!Number.isFinite(parsedBuffer) || parsedBuffer < 0) {
      setMessage('Buffer distance must be zero or a positive number.', true);
      showMapAlert('Buffer distance must be zero or a positive number.', 'warning', 'Invalid Buffer');
      return null;
    }
    return {
      name: name.trim() || 'Restricted Airspace',
      description: description.trim(),
      buffer_distance_meters: parsedBuffer
    };
  }

  function cancelNoFlyZoneModal() {
    pendingNoFlySaveRequest = null;
    const modal = document.getElementById('noFlyZoneModal');
    modal.classList.remove('visible');
    modal.setAttribute('aria-hidden', 'true');
    document.getElementById('noFlyHint').innerText = 'Save cancelled. Your draft remains on the map so you can finish or adjust it.';
  }

  async function confirmNoFlyZoneModal() {
    if (!pendingNoFlySaveRequest) return;
    const name = document.getElementById('noFlyZoneName').value.trim() || 'Restricted Airspace';
    const description = document.getElementById('noFlyZoneDescription').value.trim();
    const parsedBuffer = Number(document.getElementById('noFlyZoneBuffer').value);
    if (!Number.isFinite(parsedBuffer) || parsedBuffer < 0) {
      setMessage('Buffer distance must be zero or a positive number.', true);
      showMapAlert('Buffer distance must be zero or a positive number.', 'warning', 'Invalid Buffer');
      return;
    }

    const payload = {
      name,
      description,
      buffer_distance_meters: parsedBuffer,
      shape_type: pendingNoFlySaveRequest.shapeType,
      geometry: pendingNoFlySaveRequest.geometry,
      is_active: true
    };
    try {
      const data = await fetchJson('/no-fly-zones', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      await refreshNoFlyZones();
      clearNoFlyDrawing();
      activeNoFlyMode = null;
      resetNoFlyModeButtons();
      pendingNoFlySaveRequest = null;
      const modal = document.getElementById('noFlyZoneModal');
      modal.classList.remove('visible');
      modal.setAttribute('aria-hidden', 'true');
      setMessage(`No-fly zone "${data.zone?.name || name}" saved.`);
      showMapAlert(`No-fly zone "${data.zone?.name || name}" saved.`, 'success', 'Zone Saved');
    } catch (err) {
      setMessage(err.message || 'Failed to save no-fly zone.', true);
      showMapAlert(err.message || 'Failed to save no-fly zone.', 'error');
    }
  }

  async function refreshNoFlyZones() {
    const data = await fetchJson('/no-fly-zones');
    renderNoFlyZones(data.zones || []);
  }

  async function toggleNoFlyZoneActive(zoneId, isActive) {
    try {
      await fetchJson(`/no-fly-zones/${zoneId}/active`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: isActive })
      });
      await refreshNoFlyZones();
      setMessage(`No-fly zone ${isActive ? 'enabled' : 'disabled'}.`);
      showMapAlert(`No-fly zone ${isActive ? 'enabled' : 'disabled'}.`, 'success', 'Zone Updated');
    } catch (err) {
      setMessage(err.message || 'Failed to update no-fly zone state.', true);
      showMapAlert(err.message || 'Failed to update no-fly zone state.', 'error');
    }
  }

  async function editNoFlyZone(zoneId) {
    const zone = noFlyZones.find(item => item.id === zoneId);
    if (!zone) return;
    const details = promptNoFlyZoneDetails(zone);
    if (!details) return;
    try {
      await fetchJson(`/no-fly-zones/${zoneId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: details.name,
          description: details.description,
          buffer_distance_meters: details.buffer_distance_meters,
          is_active: zone.is_active,
          shape_type: zone.shape_type,
          geometry: zone.geometry
        })
      });
      await refreshNoFlyZones();
      setMessage(`No-fly zone "${details.name}" updated.`);
      showMapAlert(`No-fly zone "${details.name}" updated.`, 'success', 'Zone Updated');
    } catch (err) {
      setMessage(err.message || 'Failed to update no-fly zone.', true);
      showMapAlert(err.message || 'Failed to update no-fly zone.', 'error');
    }
  }

  async function deleteNoFlyZone(zoneId) {
    if (!window.confirm('Delete this no-fly zone?')) return;
    try {
      await fetchJson(`/no-fly-zones/${zoneId}`, { method: 'DELETE' });
      await refreshNoFlyZones();
      setMessage('No-fly zone deleted.');
      showMapAlert('No-fly zone deleted.', 'success', 'Zone Updated');
    } catch (err) {
      setMessage(err.message || 'Failed to delete no-fly zone.', true);
      showMapAlert(err.message || 'Failed to delete no-fly zone.', 'error');
    }
  }

  async function clearAllNoFlyZones() {
    if (!window.confirm('Delete all saved no-fly zones?')) return;
    try {
      const data = await fetchJson('/no-fly-zones', { method: 'DELETE' });
      await refreshNoFlyZones();
      clearNoFlyDrawing();
      setMessage(`Cleared ${data.deleted ?? 0} saved no-fly zone(s).`);
      showMapAlert(`Cleared ${data.deleted ?? 0} saved no-fly zone(s).`, 'success', 'Zones Cleared');
    } catch (err) {
      setMessage(err.message || 'Failed to clear saved no-fly zones.', true);
      showMapAlert(err.message || 'Failed to clear saved no-fly zones.', 'error');
    }
  }

  function finishPolygonZone() {
    if (pendingNoFlyPreview) {
      map.removeLayer(pendingNoFlyPreview);
      pendingNoFlyPreview = null;
    }
    if (activeNoFlyMode !== 'polygon' && pendingNoFlyPoints.length === 0) {
      setMessage('Choose Polygon Zone and click at least three map points first.', true);
      showMapAlert('Choose Polygon Zone and click at least three map points first.', 'warning', 'Polygon Draft Needed');
      return;
    }
    if (pendingNoFlyPoints.length < 3) {
      setMessage('Polygon no-fly zones require at least three points.', true);
      showMapAlert('Polygon no-fly zones require at least three points.', 'warning', 'Polygon Draft Needed');
      if (pendingNoFlyPoints.length >= 2) {
        pendingNoFlyPreview = L.polyline(pendingNoFlyPoints.map(point => [point.lat, point.lon]), { color: '#ef476f', weight: 2, dashArray: '5 6' }).addTo(map);
      }
      return;
    }
    if (pendingNoFlyPreview) {
      map.removeLayer(pendingNoFlyPreview);
    }
    pendingNoFlyPreview = L.polygon(pendingNoFlyPoints.map(point => [point.lat, point.lon]), {
      color: '#ef476f',
      weight: 2,
      fillColor: '#ef476f',
      fillOpacity: 0.08,
      dashArray: '5 6'
    }).addTo(map);
    openNoFlyZoneModal('polygon', { points: pendingNoFlyPoints.map(point => ({ lat: point.lat, lon: point.lon })) });
  }

  function updateSpeedControls(multiplier) {
    speedMultiplier = Number(multiplier) || 1;
    document.getElementById('speedMultiplierValue').innerText = `${speedMultiplier.toFixed(1)}x`;
    const speedSlider = document.getElementById('speedSlider');
    const speedSliderValue = document.getElementById('speedSliderValue');
    if (speedSlider) speedSlider.value = Math.round(speedMultiplier);
    if (speedSliderValue) speedSliderValue.innerText = `${Math.round(speedMultiplier)}x`;
  }

  async function setDemoSpeed(multiplier) {
    try {
      const data = await fetchJson('/speed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ multiplier })
      });
      updateSpeedControls(data.demo?.multiplier ?? multiplier);
      setMessage(`Demo flight speed set to ${(data.demo?.multiplier ?? multiplier).toFixed(1)}x.`);
    } catch (err) {
      setMessage(err.message || 'Failed to update demo speed.', true);
    }
  }

  function onSpeedSliderInput() {
    const slider = document.getElementById('speedSlider');
    document.getElementById('speedSliderValue').innerText = `${slider.value}x`;
  }

  async function commitSpeedSlider() {
    const slider = document.getElementById('speedSlider');
    await setDemoSpeed(Number(slider.value));
  }

  function triggerCelebration(message) {
    const layer = document.getElementById('celebrationLayer');
    if (!layer) return;
    layer.innerHTML = '';
    const colors = ['#2a9d8f', '#4d96ff', '#ff7f51', '#ffd166', '#8352a5', '#ef476f'];
    for (let index = 0; index < 104; index += 1) {
      const piece = document.createElement('div');
      piece.className = 'confetti-piece';
      piece.style.left = `${Math.random() * 100}%`;
      piece.style.background = colors[index % colors.length];
      piece.style.animationDelay = `${Math.random() * 0.25}s`;
      piece.style.setProperty('--drift', `${(Math.random() - 0.5) * 220}px`);
      piece.style.width = `${8 + Math.random() * 6}px`;
      piece.style.height = `${14 + Math.random() * 10}px`;
      layer.appendChild(piece);
    }
    playCelebrationSound();
    if (celebrationTimer) clearTimeout(celebrationTimer);
    celebrationTimer = setTimeout(() => {
      layer.innerHTML = '';
    }, 3200);
    if (message) {
      setMessage(message);
    }
  }

  function playCelebrationSound() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    if (!celebrationAudioContext) {
      celebrationAudioContext = new AudioContextClass();
    }
    const ctx = celebrationAudioContext;
    if (ctx.state === 'suspended') {
      ctx.resume().catch(() => {});
    }
    const now = ctx.currentTime;
    const notes = [
      { freq: 523.25, start: 0.00, duration: 0.16 },
      { freq: 659.25, start: 0.12, duration: 0.16 },
      { freq: 783.99, start: 0.24, duration: 0.2 },
      { freq: 1046.5, start: 0.44, duration: 0.34 }
    ];
    notes.forEach(note => {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      oscillator.type = 'triangle';
      oscillator.frequency.setValueAtTime(note.freq, now + note.start);
      gain.gain.setValueAtTime(0.0001, now + note.start);
      gain.gain.exponentialRampToValueAtTime(0.12, now + note.start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + note.start + note.duration);
      oscillator.connect(gain);
      gain.connect(ctx.destination);
      oscillator.start(now + note.start);
      oscillator.stop(now + note.start + note.duration + 0.02);
    });
    if (celebrationSoundTimer) clearTimeout(celebrationSoundTimer);
    celebrationSoundTimer = setTimeout(() => {}, 900);
  }

  function updateTargetMarker(lat, lon) {
    if (targetMarker) map.removeLayer(targetMarker);
    targetMarker = L.marker([lat, lon], { icon: targetIcon, title: 'Target' }).addTo(map);
    document.getElementById('tarLat').innerText = lat.toFixed(4);
    document.getElementById('tarLon').innerText = lon.toFixed(4);
    document.getElementById('targetSection').style.display = 'block';
  }

  function getBinPopupContent(item) {
    const overflowLabel = item.is_overflowing ? 'Turn Overflow Off' : 'Turn Overflow On';
    const conditionText = item.condition || (item.is_overflowing ? 'Overflowing' : 'Standard');
    const priorityText = item.priority || (item.is_overflowing ? 'High' : 'Normal');
    return `
      <div style="min-width:180px;">
        <div style="font-weight:700;color:#1d3557;margin-bottom:4px;">${item.label || item.bin_type}</div>
        <div style="font-size:12px;color:#5d6b86;margin-bottom:4px;">${item.address || 'Address pending'}</div>
        <div style="font-size:12px;color:#5d6b86;margin-bottom:2px;">Condition: ${conditionText}</div>
        <div style="font-size:12px;color:#5d6b86;margin-bottom:8px;">Priority: ${priorityText}</div>
        <button type="button" onclick="toggleBinOverflow(${item.id}, ${item.is_overflowing ? 'false' : 'true'})" style="width:100%;padding:8px 10px;border:none;border-radius:8px;background:${item.is_overflowing ? 'linear-gradient(135deg, #6c757d 0%, #495057 100%)' : 'linear-gradient(135deg, #ef476f 0%, #ff7f51 100%)'};color:#fff;font-size:12px;font-weight:700;cursor:pointer;">${overflowLabel}</button>
      </div>
    `;
  }

  function createBinIcon(binItem) {
    const style = binStyles[binItem.bin_type] || binStyles.garbage;
    const overflowBadge = binItem.is_overflowing
      ? `<div style="position:absolute;top:-4px;right:-5px;width:16px;height:16px;border-radius:50%;background:#ff7f51;border:2px solid #fff;color:#fff;font-size:11px;font-weight:700;line-height:11px;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,0.25);">!</div><div style="position:absolute;bottom:-3px;left:50%;transform:translateX(-50%);padding:1px 5px;border-radius:999px;background:#ff7f51;color:#fff;font-size:8px;font-weight:700;letter-spacing:0.4px;box-shadow:0 2px 8px rgba(0,0,0,0.22);">OVR</div>`
      : '';
    return L.divIcon({
      className: '',
      html: `<div style="width:34px;height:36px;display:flex;align-items:center;justify-content:center;position:relative;"><svg viewBox="0 0 64 64" width="30" height="30" aria-hidden="true"><path fill="#171717" d="M17 13h30l2 8H15z"/><path fill="#ffffff" d="M21 21h22l3 26a6 6 0 0 1-6 7H24a6 6 0 0 1-6-7z"/><path fill="${style.color}" d="M22 22h20l2.5 23a4.5 4.5 0 0 1-4.5 5H24a4.5 4.5 0 0 1-4.5-5z"/><rect x="26" y="10" width="12" height="4" rx="2" fill="#171717"/><path fill="#ffffff" d="M32 28l3.4 5.7H40l-5.5 9.4-3.5-5.8h-4.4z"/><path fill="#ffffff" d="M28.5 38.5l-2.8 4.6h11L32 35.7z"/><circle cx="46" cy="47.5" r="5.5" fill="#171717"/><circle cx="46" cy="47.5" r="2" fill="${style.accent}"/></svg>${overflowBadge}</div>`,
      iconSize: [34, 36],
      iconAnchor: [17, 18],
      popupAnchor: [0, -12]
    });
  }

  function renderBins(binItems) {
    latestBinsSnapshot = Array.isArray(binItems) ? binItems.map(item => ({ ...item })) : [];
    const seenIds = new Set();
    (binItems || []).forEach(item => {
      seenIds.add(item.id);
      const popup = getBinPopupContent(item);
      if (binMarkers.has(item.id)) {
        const marker = binMarkers.get(item.id);
        marker.setLatLng([item.lat, item.lon]);
        marker.setIcon(createBinIcon(item));
        marker.setPopupContent(popup);
      } else {
        const marker = L.marker([item.lat, item.lon], { icon: createBinIcon(item), title: item.label || item.bin_type }).addTo(map);
        marker.bindPopup(popup);
        binMarkers.set(item.id, marker);
      }
    });
    Array.from(binMarkers.keys()).forEach(id => {
      if (!seenIds.has(id)) {
        map.removeLayer(binMarkers.get(id));
        binMarkers.delete(id);
      }
    });
  }

  function getTruckStatusLabel(status) {
    return String(status || 'ASSIGNED').replaceAll('_', ' ');
  }

  function getTruckStatusTimestamp(item, status) {
    const normalized = String(status || '').toUpperCase();
    const timestampMap = {
      ASSIGNED: item?.assigned_at,
      EN_ROUTE: item?.en_route_at,
      ARRIVED: item?.arrived_at,
      PICKED_UP: item?.picked_up_at,
      API_PENDING_CONFIRMATION: item?.api_confirmed_at || item?.picked_up_at,
      CLOSED: item?.closed_at
    };
    return timestampMap[normalized] || '';
  }

  function getTruckTimelineTitle(item) {
    const lines = [
      `Assigned: ${formatTimestamp(item?.assigned_at)}`,
      `En route: ${formatTimestamp(item?.en_route_at)}`,
      `Arrived: ${formatTimestamp(item?.arrived_at)}`,
      `Picked up: ${formatTimestamp(item?.picked_up_at)}`,
      `API confirmed: ${formatTimestamp(item?.api_confirmed_at)}`,
      `Closed: ${formatTimestamp(item?.closed_at)}`
    ];
    return lines.join('\n');
  }

  function showImagePreview(src, altText = 'Expanded evidence preview') {
    const overlay = document.getElementById('imagePreviewOverlay');
    const image = document.getElementById('imagePreviewOverlayImg');
    if (!overlay || !image || !src) return;
    image.src = src;
    image.alt = altText;
    overlay.classList.add('visible');
    overlay.setAttribute('aria-hidden', 'false');
  }

  function hideImagePreview() {
    const overlay = document.getElementById('imagePreviewOverlay');
    const image = document.getElementById('imagePreviewOverlayImg');
    if (!overlay || !image) return;
    overlay.classList.remove('visible');
    overlay.setAttribute('aria-hidden', 'true');
    image.removeAttribute('src');
  }

  function renderEvidencePreview(image, label, copyText, emptyText = 'No image is attached yet.') {
    if (!image) {
      return `<div class="truck-timestamp-note">${escapeHtml(emptyText)}</div>`;
    }
    return `
      <div class="evidence-wrap">
        <div class="evidence-preview">
          <img
            class="evidence-thumb"
            src="/inspection-images/${image.image_id}/file"
            alt="Thumbnail preview for ${escapeHtml(label)}"
            tabindex="0"
            onmouseenter="showImagePreview(this.src, this.alt)"
            onmouseleave="hideImagePreview()"
            onfocus="showImagePreview(this.src, this.alt)"
            onblur="hideImagePreview()">
          <div class="evidence-thumb-copy">${escapeHtml(copyText)}</div>
        </div>
        <div class="evidence-meta">
          <span class="meta-pill">Source: ${escapeHtml(image.provider_name || 'simulated')}</span>
          <span class="meta-pill">Capture: ${escapeHtml(image.capture_status || 'COMPLETED')}</span>
          <span class="meta-pill">Privacy: ${escapeHtml(image.privacy_processing_status || 'PROCESSED')}</span>
          <span class="meta-pill">Capture confidence: ${Math.round((Number(image.confidence_score) || 0) * 100)}%</span>
          ${image.people_detected ? '<span class="meta-pill">People detected</span>' : ''}
          ${image.faces_detected ? '<span class="meta-pill">Faces detected</span>' : ''}
          ${image.license_plates_detected ? '<span class="meta-pill">Licence plate detected</span>' : ''}
          ${image.moving_vehicle_detected ? '<span class="meta-pill">Moving vehicle detected</span>' : ''}
        </div>
      </div>
    `;
  }

  function getTruckStatusClass(status) {
    return `priority-${String(status || 'normal').toLowerCase().replaceAll('_pending_confirmation', 'high').replaceAll('api_pending_confirmation', 'medium').replaceAll('closed', 'normal').replaceAll('en_route', 'high').replaceAll('arrived', 'medium').replaceAll('picked_up', 'critical').replaceAll('assigned', 'normal')}`;
  }

  function syncSelectedDispatchEntries(addresses) {
    const validIds = new Set(
      (addresses || [])
        .filter(item => item?.entry_id && !(item?.truck_assignment && !item.truck_assignment.is_closed))
        .map(item => Number(item.entry_id))
    );
    selectedDispatchEntryIds = new Set([...selectedDispatchEntryIds].filter(entryId => validIds.has(Number(entryId))));
    const counter = document.getElementById('selectedDispatchCount');
    const assignButton = document.getElementById('assignTruckDispatchButton');
    if (counter) counter.innerText = `${selectedDispatchEntryIds.size} selected`;
    if (assignButton) assignButton.disabled = selectedDispatchEntryIds.size === 0;
  }

  function toggleDispatchSelection(entryId, checked) {
    const safeId = Number(entryId);
    if (!Number.isFinite(safeId)) return;
    if (checked) selectedDispatchEntryIds.add(safeId);
    else selectedDispatchEntryIds.delete(safeId);
    syncSelectedDispatchEntries(Array.from(document.querySelectorAll('[data-dispatch-entry-id]')).map(element => ({ entry_id: Number(element.getAttribute('data-dispatch-entry-id')) })));
  }

  async function assignSelectedToTruckDispatch() {
    const truckInput = document.getElementById('truckNumberInput');
    const routeInput = document.getElementById('routeNumberInput');
    const truckNumber = truckInput?.value?.trim() || '';
    const routeNumber = routeInput?.value?.trim() || '';
    if (!selectedDispatchEntryIds.size) {
      setMessage('Select at least one unpicked bin from Flight Summary before assigning a truck.', true);
      return;
    }
    if (!truckNumber || !routeNumber) {
      setMessage('Truck number and route number are both required.', true);
      return;
    }
    try {
      const data = await fetchJson('/truck-dispatch/assign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entry_ids: Array.from(selectedDispatchEntryIds),
          truck_number: truckNumber,
          route_number: routeNumber
        })
      });
      selectedDispatchEntryIds.clear();
      truckInput.value = '';
      routeInput.value = '';
      latestTruckDispatch = data.truck_dispatch || latestTruckDispatch;
      updateDispatchSummary(data.dispatch?.last_summary || null, data.dispatch?.mission || null);
      renderTruckDispatch(latestTruckDispatch);
      showTopTab('truck');
      setMessage(data.message || 'Truck dispatch assignment created.');
    } catch (err) {
      setMessage(err.message || 'Failed to assign truck dispatch.', true);
    }
  }

  async function advanceTruckDispatchItem(itemId) {
    try {
      const data = await fetchJson(`/truck-dispatch/items/${itemId}/advance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      latestTruckDispatch = data.truck_dispatch || latestTruckDispatch;
      updateDispatchSummary(data.dispatch?.last_summary || null, data.dispatch?.mission || null);
      renderTruckDispatch(latestTruckDispatch);
      setMessage(data.message || 'Truck workflow advanced.');
    } catch (err) {
      setMessage(err.message || 'Failed to advance truck workflow.', true);
    }
  }

  async function confirmTruckDispatchApi(itemId) {
    try {
      const data = await fetchJson(`/truck-dispatch/items/${itemId}/api-confirmation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation_status: 'CONFIRMED' })
      });
      latestTruckDispatch = data.truck_dispatch || latestTruckDispatch;
      updateDispatchSummary(data.dispatch?.last_summary || null, data.dispatch?.mission || null);
      renderTruckDispatch(latestTruckDispatch);
      setMessage(data.message || 'Truck pickup closure confirmed.');
    } catch (err) {
      setMessage(err.message || 'Failed to confirm truck closure.', true);
    }
  }

  async function deleteTruckDispatchItem(itemId) {
    try {
      const data = await fetchJson(`/truck-dispatch/items/${itemId}`, {
        method: 'DELETE'
      });
      latestTruckDispatch = data.truck_dispatch || latestTruckDispatch;
      updateDispatchSummary(data.dispatch?.last_summary || null, data.dispatch?.mission || null);
      renderTruckDispatch(latestTruckDispatch);
      setMessage(data.message || 'Truck dispatch item removed.');
    } catch (err) {
      setMessage(err.message || 'Failed to delete truck dispatch item.', true);
    }
  }

  function promptTruckClosureImageUpload(itemId) {
    const input = document.getElementById(`truckClosureInput-${itemId}`);
    if (input) input.click();
  }

  async function handleTruckClosureImageSelected(itemId, input) {
    const file = input?.files?.[0];
    if (!file) return;
    try {
      const base64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error('Unable to read the selected closure image.'));
        reader.readAsDataURL(file);
      });
      const data = await fetchJson(`/truck-dispatch/items/${itemId}/api-confirmation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          confirmation_status: 'CONFIRMED',
          image_base64: base64,
          file_name: file.name,
          mime_type: file.type || 'image/jpeg'
        })
      });
      latestTruckDispatch = data.truck_dispatch || latestTruckDispatch;
      updateDispatchSummary(data.dispatch?.last_summary || null, data.dispatch?.mission || null);
      renderTruckDispatch(latestTruckDispatch);
      setMessage(data.message || 'Truck pickup closure confirmed with image evidence.');
    } catch (err) {
      setMessage(err.message || 'Failed to attach truck closure image.', true);
    } finally {
      input.value = '';
    }
  }

  function renderTruckDispatch(truckDispatch) {
    latestTruckDispatch = truckDispatch || { batches: [], open_item_count: 0 };
    const batches = Array.isArray(latestTruckDispatch?.batches) ? latestTruckDispatch.batches : [];
    const openCount = Number(latestTruckDispatch?.open_item_count || 0);
    document.getElementById('truckBatchCount').innerText = String(batches.length);
    document.getElementById('truckOpenCount').innerText = String(openCount);
    document.getElementById('truckDispatchSummaryCopy').innerHTML = batches.length
      ? `<strong>Truck workflow:</strong> ${openCount} pickup request(s) remain open across ${batches.length} truck run(s).`
      : '<strong>Truck workflow:</strong> Assign surveyed bins from Flight Summary to start the pickup route.';
    updateTruckTabBadge(openCount);
    const emptyState = document.getElementById('truckDispatchEmptyState');
    const batchList = document.getElementById('truckDispatchBatchList');
    batchList.innerHTML = '';
    if (!batches.length) {
      emptyState.style.display = 'block';
      batchList.style.display = 'none';
      return;
    }
    emptyState.style.display = 'none';
    batchList.style.display = 'flex';
    batches.forEach(batch => {
      const batchCard = document.createElement('div');
      batchCard.className = 'truck-batch-card';
      const itemsMarkup = (batch.items || []).map(item => {
        const labelMarkup = renderDispatchBinLabel(item.label, inferBinTypeFromLabel(item.label), false);
        const stepsMarkup = (item.workflow_steps || []).map(step => `
          <div class="truck-step ${step.state}" title="${escapeHtml(getTruckStatusTimestamp(item, step.status) ? `${getTruckStatusLabel(step.status)}: ${formatTimestamp(getTruckStatusTimestamp(item, step.status))}` : `${getTruckStatusLabel(step.status)}: waiting`)}">${escapeHtml(getTruckStatusLabel(step.status))}</div>
        `).join('');
        const advanceDisabled = item.status === 'API_PENDING_CONFIRMATION' || item.status === 'CLOSED';
        const confirmDisabled = item.status !== 'API_PENDING_CONFIRMATION';
        const apiImage = item.api_image || null;
        const surveyImage = item.survey_image || null;
        const surveyEvidenceMarkup = renderEvidencePreview(
          surveyImage,
          item.label,
          'Hover over the thumbnail to preview the full survey frontage image captured by the drone.',
          'No survey frontage image is available for this dispatch item yet.'
        );
        const closureEvidenceMarkup = renderEvidencePreview(
          apiImage,
          item.label,
          'Hover over the thumbnail to preview the full truck pickup confirmation image.',
          'No truck closure image has been attached yet. The API closure step can include a base64 image from the ESP32-CAM module.'
        );
        return `
          <div class="truck-item-card">
            <div class="truck-item-topline">
              <div class="truck-item-title">${labelMarkup}</div>
              <span class="priority-chip ${getTruckStatusClass(item.status)}">${escapeHtml(getTruckStatusLabel(item.status))}</span>
            </div>
            <div class="truck-item-meta">
              <span class="truck-badge" title="${escapeHtml(getTruckTimelineTitle(item))}">Flight #${escapeHtml(item.flight_run_id || item.flight_mission_id || 'pending')}</span>
              <span class="truck-badge">Bin Request ${escapeHtml(item.request_id || 'pending')}</span>
              <span class="truck-badge" title="${escapeHtml(getTruckTimelineTitle(item))}">Assigned ${escapeHtml(formatTimestamp(item.assigned_at))}</span>
              ${item.api_confirmation_status ? `<span class="truck-badge">API ${escapeHtml(item.api_confirmation_status)}</span>` : ''}
            </div>
            <div class="address-item-copy">${escapeHtml(item.address || 'Address pending')}</div>
            <div class="address-item-copy">${Number(item.lat || 0).toFixed(5)}, ${Number(item.lon || 0).toFixed(5)}</div>
            <div class="truck-step-track">${stepsMarkup}</div>
            <details class="collapsible-evidence" open>
              <summary>Survey Frontage Image</summary>
              <div class="collapsible-evidence-content">${surveyEvidenceMarkup}</div>
            </details>
            <details class="collapsible-evidence">
              <summary>Truck Closure Image</summary>
              <div class="collapsible-evidence-content">${closureEvidenceMarkup}</div>
            </details>
            <input id="truckClosureInput-${item.item_id}" type="file" accept="image/*" style="display:none" onchange="handleTruckClosureImageSelected(${item.item_id}, this)">
            <div class="truck-item-actions">
              <button type="button" class="truck-action-button truck-action-advance" onclick="advanceTruckDispatchItem(${item.item_id})" ${advanceDisabled ? 'disabled' : ''}>Advance To Next Step</button>
              <button type="button" class="truck-action-button truck-action-upload" onclick="promptTruckClosureImageUpload(${item.item_id})" ${item.status === 'CLOSED' ? 'disabled' : ''}>Upload Closure Image</button>
              <button type="button" class="truck-action-button truck-action-confirm" onclick="confirmTruckDispatchApi(${item.item_id})" ${confirmDisabled ? 'disabled' : ''}>Receive API Closure</button>
              <button type="button" class="truck-action-button truck-action-delete" onclick="deleteTruckDispatchItem(${item.item_id})">Delete Item</button>
            </div>
          </div>
        `;
      }).join('');
      batchCard.innerHTML = `
        <div class="truck-batch-head">
          <div>
            <div class="truck-batch-title">Truck ${escapeHtml(batch.truck_number)} · Route ${escapeHtml(batch.route_number)}</div>
            <div class="truck-batch-copy">Batch ${batch.batch_id} · Created ${escapeHtml(formatTimestamp(batch.created_at))} · ${batch.open_item_count} pickup(s) still open.</div>
          </div>
          <span class="priority-chip ${getTruckStatusClass(batch.status)}">${escapeHtml(getTruckStatusLabel(batch.status))}</span>
        </div>
        <div class="truck-item-list">${itemsMarkup}</div>
      `;
      batchList.appendChild(batchCard);
    });
  }

  function inferBinTypeFromLabel(label) {
    const lower = String(label || '').toLowerCase();
    if (lower.includes('green')) return 'green';
    if (lower.includes('blue')) return 'blue';
    return 'garbage';
  }

  function updateDispatchSummary(summary, mission = null) {
    const historyEntries = Array.isArray(summary?.history_entries) ? summary.history_entries : [];
    const hasHistory = historyEntries.length > 0;
    const counts = (hasHistory ? summary?.history_counts : summary?.counts) || { green: 0, blue: 0, garbage: 0 };
    const totalBins = hasHistory ? (summary?.history_total ?? historyEntries.length) : (summary?.total_bins ?? 0);
    const priorityFormula = summary?.priority_formula || 'Priority formula will appear after the survey results are available.';
    const summaryCapturedAt = summary?.captured_at || '';
    const summaryDroneId = summary?.drone_id || mission?.drone_id || 'DRONE-001';
    const missionStatus = String(mission?.status || '').toLowerCase();
    const completedWaypoints = mission?.completed_waypoints ?? 0;
    const totalWaypoints = mission?.total_waypoints ?? 0;
    const surveyComplete = totalBins > 0
      && totalWaypoints > 0
      && completedWaypoints >= totalWaypoints
      && ['completed', 'complete', 'arrived', 'done'].includes(missionStatus);
    document.getElementById('dispatchGreenCount').innerText = counts.green ?? 0;
    document.getElementById('dispatchBlueCount').innerText = counts.blue ?? 0;
    document.getElementById('dispatchGarbageCount').innerText = counts.garbage ?? 0;
    document.getElementById('dispatchTotalCount').innerText = totalBins;
    document.getElementById('dispatchDroneId').innerText = summaryDroneId;
    document.getElementById('dispatchMessage').innerText = mission?.message || summary?.dispatch_message || 'Select a zone to calculate the number of bins and the addresses for dispatch.';
    document.getElementById('flyZoneButton').disabled = !selectedZone;
    document.getElementById('dispatchSurveyProgress').innerText = `${completedWaypoints} / ${totalWaypoints}`;
    document.getElementById('dispatchRunTimestamp').innerHTML = (surveyComplete || hasHistory) && summaryCapturedAt
      ? `<strong>Survey Run:</strong> ${escapeHtml(formatTimestamp(summaryCapturedAt))}`
      : '<strong>Survey Run:</strong> Awaiting completed zone survey.';
    document.getElementById('dispatchPriorityFormula').innerText = priorityFormula;
    document.getElementById('dispatchPickupNote').innerText = hasHistory
      ? `${totalBins} saved dispatch entr${totalBins === 1 ? 'y is' : 'ies are'} awaiting pickup or review.`
      : surveyComplete
        ? `${totalBins} bin(s) are awaiting pickup from the completed zone survey.`
        : 'The pickup total appears on the Flight Summary tab after the survey is fully complete.';
    const list = document.getElementById('dispatchAddressList');
    const emptyState = document.getElementById('dispatchEmptyState');
    list.innerHTML = '';
    const addresses = hasHistory ? historyEntries : (surveyComplete ? (summary?.addresses || []) : []);
    syncSelectedDispatchEntries(addresses);
    addresses.forEach(item => {
      const card = document.createElement('div');
      card.className = 'address-item';
      if (item.entry_id) {
        card.setAttribute('data-dispatch-entry-id', String(item.entry_id));
      }
      const priorityLevel = String(item.priority || 'NORMAL').toUpperCase();
      const priorityClass = `priority-${priorityLevel.toLowerCase()}`;
      const conditionText = String(item.condition || 'standard').replaceAll('_', ' ');
      const isOverflowing = String(item.condition || '').toLowerCase() === 'overflowing';
      const activeTruckAssignment = item.truck_assignment && !item.truck_assignment.is_closed ? item.truck_assignment : null;
      const requestState = item.duplicate_open_request
        ? 'Duplicate open request avoided'
        : item.has_open_request
          ? `Open request ${item.open_request_id || item.request_id || ''}`.trim()
          : `Request ${item.request_id || 'pending'}`;
      const confidencePercent = Math.round((Number(item.inspection_confidence ?? 0.9) || 0.9) * 100);
      const image = item.latest_image || null;
      const runTimestamp = item.captured_at || summaryCapturedAt || '';
      const labelMarkup = renderDispatchBinLabel(item.label, item.bin_type, isOverflowing);
      const selectionMarkup = item.entry_id ? `
        <div class="dispatch-select-row">
          <input type="checkbox" aria-label="Select ${escapeHtml(item.label)} for truck dispatch"
            ${activeTruckAssignment ? 'disabled' : ''}
            ${selectedDispatchEntryIds.has(Number(item.entry_id)) ? 'checked' : ''}
            onchange="toggleDispatchSelection(${item.entry_id}, this.checked)">
          <span class="dispatch-select-copy">
            ${activeTruckAssignment
              ? `Already assigned to Truck ${escapeHtml(activeTruckAssignment.truck_number)} / Route ${escapeHtml(activeTruckAssignment.route_number)}.`
              : 'Select this unpicked bin to assign a truck route from Flight Summary.'}
          </span>
        </div>
      ` : '';
      const evidencePreviewMarkup = renderEvidencePreview(
        image,
        item.label,
        'Hover over the thumbnail to preview the full processed evidence image.',
        'No processed inspection image is attached to this request yet.'
      );
      const evidenceActionsMarkup = `
        <div class="evidence-footer">
          <div class="evidence-status">${escapeHtml((image && image.review_status) || 'PENDING_REVIEW')}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;">
            <button type="button" class="demo-upload-button" onclick="promptDemoImageUpload(${item.id})">Upload Demo Image</button>
            ${image ? `<button type="button" class="review-button" onclick="toggleImageManualReview(${image.image_id}, ${image.manual_review_required ? 'false' : 'true'})">
              ${image.manual_review_required ? 'Clear Manual Review' : 'Flag Manual Review'}
            </button>` : ''}
            ${item.entry_id ? `<button type="button" class="dispatch-delete-button" onclick="deleteDispatchEntry(${item.entry_id})">Delete Entry</button>` : ''}
          </div>
        </div>
      `;
      const evidenceBlock = activeTruckAssignment
        ? `
          <details class="collapsible-evidence">
            <summary>Survey Evidence And Image Details</summary>
            <div class="collapsible-evidence-content">
              <input id="demoImageInput-${item.id}" type="file" accept="image/*" style="display:none" onchange="handleDemoImageSelected(${item.id}, this)">
              ${evidencePreviewMarkup}
              ${evidenceActionsMarkup}
            </div>
          </details>
        `
        : `
          <div>
            <input id="demoImageInput-${item.id}" type="file" accept="image/*" style="display:none" onchange="handleDemoImageSelected(${item.id}, this)">
            ${evidencePreviewMarkup}
            ${evidenceActionsMarkup}
          </div>
        `;
      card.innerHTML = `
        <div class="address-item-topline">
          <div class="address-item-title">${labelMarkup}</div>
          <span class="priority-chip ${priorityClass}">${priorityLevel} - ${item.priority_score}</span>
        </div>
        <div class="address-item-copy">${item.address}</div>
        <div class="address-item-copy">${item.lat.toFixed(5)}, ${item.lon.toFixed(5)}</div>
        ${selectionMarkup}
        <div class="address-item-meta">
          <span class="meta-pill">Condition: ${conditionText}</span>
          <span class="meta-pill">Location: ${String(item.location_importance || 'residential')}</span>
          <span class="meta-pill">Confidence: ${confidencePercent}%</span>
          ${runTimestamp ? `<span class="meta-pill">Survey run: ${escapeHtml(formatTimestamp(runTimestamp))}</span>` : ''}
          <span class="meta-pill">Drone: ${escapeHtml(item.drone_id || summaryDroneId)}</span>
          ${activeTruckAssignment ? `<span class="meta-pill">Truck ${escapeHtml(activeTruckAssignment.truck_number)} · Route ${escapeHtml(activeTruckAssignment.route_number)} · ${escapeHtml(getTruckStatusLabel(activeTruckAssignment.status))}</span>` : ''}
          ${item.loose_garbage_detected ? '<span class="meta-pill">Loose garbage detected</span>' : ''}
          <span class="meta-pill">${requestState}</span>
        </div>
        ${evidenceBlock}
      `;
      list.appendChild(card);
    });
    const summaryCard = document.querySelector('.dispatch-summary-card .dispatch-summary');
    let selectBar = document.getElementById('dispatchSelectBar');
    if (!selectBar && summaryCard) {
      selectBar = document.createElement('div');
      selectBar.className = 'dispatch-select-bar';
      selectBar.id = 'dispatchSelectBar';
      selectBar.innerHTML = `
        <div class="dispatch-select-copy">Select unpicked bins here, assign a truck number and route number, then continue the pickup workflow in the Truck Dispatch tab.</div>
        <div class="dispatch-select-grid">
          <input id="truckNumberInput" type="text" placeholder="Truck #">
          <input id="routeNumberInput" type="text" placeholder="Route #">
        </div>
        <div class="address-item-meta" style="justify-content:space-between;align-items:center;">
          <span class="meta-pill" id="selectedDispatchCount">0 selected</span>
          <button type="button" class="mini-button assign-button" id="assignTruckDispatchButton" onclick="assignSelectedToTruckDispatch()" disabled>Assign To Truck Dispatch</button>
        </div>
      `;
      const formulaBox = document.getElementById('dispatchPriorityFormula');
      if (formulaBox) {
        formulaBox.insertAdjacentElement('beforebegin', selectBar);
      }
    }
    syncSelectedDispatchEntries(addresses);
    renderTruckDispatch(summary?.truck_dispatch || latestTruckDispatch);
    if (addresses.length) {
      emptyState.style.display = 'none';
      list.style.display = 'flex';
    } else {
      list.style.display = 'none';
      emptyState.style.display = 'block';
      emptyState.innerHTML = surveyComplete
        ? '<strong>No bins found</strong>The zone survey completed successfully, but no bins are currently waiting in the selected zone.'
        : hasHistory
          ? '<strong>No saved entries</strong>Dispatch history is empty right now.'
        : '<strong>Survey pending</strong>Fly the selected zone to collect the verified list of bin locations and addresses for dispatch.';
    }
    updateDispatchTabBadge(hasHistory ? totalBins : (surveyComplete ? totalBins : 0));
  }

  function updateDispatchTabBadge(count) {
    const badge = document.getElementById('dispatchTabBadge');
    if (!badge) return;
    const safeCount = Math.max(0, Number(count) || 0);
    badge.innerText = String(safeCount);
    badge.classList.toggle('visible', safeCount > 0);
  }

  function updateTruckTabBadge(count) {
    const badge = document.getElementById('truckTabBadge');
    if (!badge) return;
    const safeCount = Math.max(0, Number(count) || 0);
    badge.innerText = String(safeCount);
    badge.classList.toggle('visible', safeCount > 0);
  }

  function renderDispatchBinLabel(label, binType, isOverflowing) {
    const safeLabel = escapeHtml(label || 'Bin');
    const icon = getDispatchBinIconSvg(binType, isOverflowing);
    return `
      <span class="address-item-title-row">
        <span class="dispatch-bin-icon" aria-hidden="true">${icon}</span>
        <span>${safeLabel}</span>
        ${isOverflowing ? '<span class="overflow-alert-badge" aria-label="Overflowing bin" title="Overflowing bin">!</span>' : ''}
      </span>
    `;
  }

  function getDispatchBinIconSvg(binType, isOverflowing) {
    const style = binStyles[binType] || binStyles.garbage;
    const accent = isOverflowing ? '#ffb703' : style.accent;
    const glow = isOverflowing ? '#ff7f51' : style.color;
    return `
      <svg viewBox="0 0 24 24" role="img" aria-hidden="true">
        <path d="M8 3h8l1 2h3v2H4V5h3l1-2z" fill="#1f1f1f"></path>
        <path d="M7 8h10l-.8 10.2A2 2 0 0 1 14.2 20H9.8a2 2 0 0 1-1.99-1.8L7 8z" fill="${style.color}"></path>
        <rect x="9" y="10" width="6" height="7" rx="1.5" fill="#ffffff"></rect>
        <path d="M12 11.1l.95 1.63h-1.9L12 11.1zm-1.65 2.2h3.3l-1.65 2.86-1.65-2.86zm-1.05.55l.96 1.66h-1.9l.94-1.66zm4.44 0l.95 1.66h-1.9l.95-1.66z" fill="${accent}"></path>
        <circle cx="18.4" cy="18.2" r="2.1" fill="${glow}"></circle>
        <path d="M18.4 16.8v2.1" stroke="#ffffff" stroke-width="1.4" stroke-linecap="round"></path>
        <circle cx="18.4" cy="19.7" r="0.8" fill="#ffffff"></circle>
      </svg>
    `;
  }

  function formatTimestamp(value) {
    if (!value) return 'Unknown';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat('en-CA', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    }).format(date);
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  async function toggleImageManualReview(imageId, required) {
    try {
      const response = await fetchJson(`/inspection-images/${imageId}/manual-review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ manual_review_required: required })
      });
      await refreshDispatchData();
      setMessage(response.image?.manual_review_required ? 'Image flagged for manual review.' : 'Image cleared from manual review.');
    } catch (err) {
      setMessage(err.message || 'Failed to update image review status.', true);
    }
  }

  function promptDemoImageUpload(binId) {
    const input = document.getElementById(`demoImageInput-${binId}`);
    if (input) input.click();
  }

  async function handleDemoImageSelected(binId, input) {
    const file = input?.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('image', file);
    try {
      const response = await fetch(`/bins/${binId}/demo-image`, {
        method: 'POST',
        body: formData
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.message || `Upload failed with ${response.status}`);
      }
      await refreshDispatchData();
      setMessage(data.message || 'Demo image attached.');
    } catch (err) {
      setMessage(err.message || 'Failed to upload demo image.', true);
    } finally {
      input.value = '';
    }
  }

  async function deleteDispatchEntry(entryId) {
    try {
      const data = await fetchJson(`/dispatch-entries/${entryId}`, { method: 'DELETE' });
      updateDispatchSummary(data.dispatch?.last_summary || null, data.dispatch?.mission || null);
      setMessage(data.deleted?.label ? `${data.deleted.label} removed from dispatch history.` : 'Dispatch entry removed.');
    } catch (err) {
      setMessage(err.message || 'Failed to delete dispatch entry.', true);
    }
  }

  function setMapMode(mode) {
    activeMapMode = mode;
    activeNoFlyMode = null;
    pendingZoneCorner = null;
    clearNoFlyDrawing();
    resetNoFlyModeButtons();
    document.querySelectorAll('.mode-button').forEach(button => button.classList.remove('active'));
    const buttonMap = { green: 'modeGreen', blue: 'modeBlue', garbage: 'modeGarbage', zone: 'modeZone' };
    if (buttonMap[mode]) document.getElementById(buttonMap[mode]).classList.add('active');
    const hints = {
      green: 'Click the map to place a green waste bin and record its address.',
      blue: 'Click the map to place a blue recycling bin and record its address.',
      garbage: 'Click the map to place a garbage bin and record its address.',
      zone: 'Click one corner, then click the opposite corner to define the drone dispatch zone.'
    };
    document.getElementById('toolHint').innerText = hints[mode] || 'Pick a tool, then click the GPS map to place bins or choose a two-corner dispatch zone.';
  }

  function clearMapMode() {
    activeMapMode = null;
    activeNoFlyMode = null;
    pendingZoneCorner = null;
    clearNoFlyDrawing();
    resetNoFlyModeButtons();
    document.querySelectorAll('.mode-button').forEach(button => button.classList.remove('active'));
    document.getElementById('toolHint').innerText = 'Pick a tool, then click the GPS map to place bins or choose a two-corner dispatch zone.';
    document.getElementById('noFlyHint').innerText = 'Choose a no-fly-zone shape, click the map to draw it, then name and save it.';
  }

  async function reverseGeocode(lat, lon) {
    const response = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`, { headers: { 'Accept': 'application/json' } });
    if (!response.ok) throw new Error(`Reverse geocode failed with ${response.status}`);
    const data = await response.json();
    return data.display_name || `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  }

  async function placeWasteBin(binType, lat, lon) {
    const style = binStyles[binType];
    const fallbackAddress = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    setMessage(`Placing ${style.label.toLowerCase()}...`);
    try {
      const created = await fetchJson('/bins', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bin_type: binType, lat, lon, address: fallbackAddress, label: style.label })
      });
      if (created?.bin) {
        renderBins([...latestBinsSnapshot, created.bin]);
      }
      try {
        await refreshDispatchData();
      } catch (refreshErr) {
        console.error('Dispatch refresh after bin placement failed.', refreshErr);
        if (created?.bin) {
          renderBins([...latestBinsSnapshot, created.bin]);
        }
      }
      setMessage(`${style.label} placed. Resolving address...`);
      reverseGeocode(lat, lon)
        .then(async address => {
          await fetchJson(`/bins/${created.bin.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ address })
          });
          try {
            await refreshDispatchData();
          } catch (refreshErr) {
            console.error('Dispatch refresh after reverse geocode failed.', refreshErr);
          }
          setMessage(`${style.label} placed at ${address}`);
        })
        .catch(err => {
          console.error(err);
          setMessage(`${style.label} placed at ${fallbackAddress}`);
        });
    } catch (err) {
      setMessage(err.message || 'Failed to place waste bin.', true);
    }
  }

  async function toggleBinOverflow(binId, shouldOverflow) {
    try {
      const data = await fetchJson(`/bins/${binId}/overflow`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_overflowing: shouldOverflow })
      });
      console.log('Overflow update response', data);
      await refreshDispatchData();
      if (binMarkers.has(binId)) {
        binMarkers.get(binId).openPopup();
      }
      setMessage(data.message || 'Bin overflow state updated.');
    } catch (err) {
      setMessage(err.message || 'Failed to update bin overflow state.', true);
    }
  }

  function drawZoneRectangle(zone) {
    if (!zone) return;
    if (zoneRectangle) map.removeLayer(zoneRectangle);
    zoneRectangle = L.rectangle([[zone.south, zone.west], [zone.north, zone.east]], {
      color: '#7a8f32',
      weight: 2,
      fillColor: '#a4c24d',
      fillOpacity: 0.14
    }).addTo(map);
  }

  async function submitZone(zone) {
    suppressStatusPollingError = true;
    try {
      const data = await fetchJson('/zone-summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(zone)
      });
      if (data.summary) {
        selectedZone = data.summary.zone || zone;
        drawZoneRectangle(zone);
        updateDispatchSummary(data.summary, { status: 'ready', message: 'Zone saved. Start the survey flight to collect dispatch details.', completed_waypoints: 0, total_waypoints: 0 });
        setMessage('Zone saved. Use "Fly Drone To Zone" to survey the full area before dispatch details are generated.');
      }
    } finally {
      suppressStatusPollingError = false;
    }
  }

  function getZoneCenter(zone) {
    if (!zone) return null;
    return { lat: (zone.south + zone.north) / 2, lon: (zone.west + zone.east) / 2 };
  }

  function resetNavigationProgressUI() {
    document.getElementById('progressSection').style.display = 'none';
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressPercent').innerText = '0%';
    document.getElementById('distanceValue').innerText = '0.00';
    document.getElementById('timeElapsed').innerText = 'Time: 0s';
  }

  async function flyToSelectedZone() {
    if (!selectedZone) return setMessage('Define a zone before dispatching the drone.', true);
    const center = getZoneCenter(selectedZone);
    if (!center || !Number.isFinite(center.lat) || !Number.isFinite(center.lon)) return setMessage('Selected zone does not have valid center coordinates.', true);
    suppressStatusPollingError = true;
    updateTargetMarker(center.lat, center.lon);
      updateStatus('flying', 'En Route...');
      setMessage('Dispatching drone to the selected zone...');
    try {
      const data = await fetchJson('/zone-fly', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(selectedZone)
      });
      if (data.rerouted) {
        updateStatus('rerouting', 'Rerouting...');
      } else {
        updateStatus('flying', 'En Route...');
      }
      if (data.summary) {
        selectedZone = data.summary.zone || selectedZone;
        updateDispatchSummary(data.summary, { status: 'in_progress', message: data.summary.dispatch_message, completed_waypoints: 0, total_waypoints: 0 });
      }
      if (data.center) updateTargetMarker(Number(data.center.lat), Number(data.center.lon));
      navigationStartTime = Date.now();
      navigationStartDistance = 0;
      setMessage(data.message || (data.rerouted ? 'Drone is rerouting to the selected zone.' : 'Drone is en route to the selected zone.'));
      if (Array.isArray(data.warnings) && data.warnings.length) {
        showMapAlert(data.warnings[0], 'warning');
      } else if (data.rerouted) {
        showMapAlert('Route recalculated to avoid restricted airspace.', 'warning');
      } else {
        showMapAlert(data.message || 'Drone dispatched to survey the zone.', 'success', 'Mission Started');
      }
    } catch (err) {
      updateStatus('error', 'Zone Dispatch Error');
      resetNavigationProgressUI();
      setMessage(err.message || 'Failed to fly drone to selected zone.', true);
      showMapAlert(err.message || 'Failed to fly drone to selected zone.', 'error');
    } finally {
      suppressStatusPollingError = false;
    }
  }

  async function refreshDispatchData() {
    const data = await fetchJson('/bins');
    renderBins(data.bins || []);
    if (data.last_zone) { selectedZone = data.last_zone; drawZoneRectangle(data.last_zone); } else { selectedZone = null; }
    updateDispatchSummary(data.last_summary || null, data.mission || null);
  }

  async function clearAllBins() {
    await fetchJson('/bins', { method: 'DELETE' });
    renderBins([]);
    updateDispatchSummary(null, null);
    if (zoneRectangle) { map.removeLayer(zoneRectangle); zoneRectangle = null; }
    selectedZone = null;
    pendingZoneCorner = null;
    setMessage('All waste bins and dispatch selections were cleared.');
  }

  function updateRouteLine(routePoints) {
    if (routeLine) { map.removeLayer(routeLine); routeLine = null; }
    if (!Array.isArray(routePoints) || routePoints.length < 2) return;
    const latLngs = routePoints.filter(point => Number.isFinite(point.lat) && Number.isFinite(point.lon)).map(point => [point.lat, point.lon]);
    if (latLngs.length < 2) return;
    routeLine = L.polyline(latLngs, { color: '#2a9d8f', weight: 4, opacity: 0.85, dashArray: '8 10' }).addTo(map);
  }

  function updateFeedbackLoop(feedback, weather) {
    const fb = feedback || {};
    const w = weather || {};
    document.getElementById('rainValue').innerText = `${w.rain ?? 0}%`;
    document.getElementById('windValue').innerText = `${w.wind ?? 0}%`;
    document.getElementById('snowValue').innerText = `${w.snow ?? 0}%`;
    document.getElementById('rainSlider').value = w.rain ?? 0;
    document.getElementById('windSlider').value = w.wind ?? 0;
    document.getElementById('snowSlider').value = w.snow ?? 0;
    document.getElementById('rainSliderValue').innerText = `${w.rain ?? 0}%`;
    document.getElementById('windSliderValue').innerText = `${w.wind ?? 0}%`;
    document.getElementById('snowSliderValue').innerText = `${w.snow ?? 0}%`;
    document.getElementById('loopCycle').innerText = fb.cycle ?? 0;
    document.getElementById('rerouteCount').innerText = fb.reroute_count ?? 0;
    document.getElementById('loopReason').innerText = fb.reason || 'Feedback loop idle.';
    document.querySelectorAll('[data-loop-step]').forEach(step => step.classList.toggle('active', step.dataset.loopStep === (fb.stage || 'repeat')));
    updateRouteLine(fb.active_route);
  }

  function getWeatherPayload() {
    return { rain: Number(document.getElementById('rainSlider').value), wind: Number(document.getElementById('windSlider').value), snow: Number(document.getElementById('snowSlider').value) };
  }

  async function pushWeatherToServer(showMessage = false) {
    const payload = getWeatherPayload();
    try {
      const data = await fetchJson('/weather', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (data.weather) updateFeedbackLoop({ stage: 'sense' }, data.weather);
      if (showMessage) setMessage(`Weather synced to AirSim: rain ${payload.rain}%, wind ${payload.wind}%, snow ${payload.snow}%.`);
    } catch (err) {
      setMessage(err.message || 'Failed to sync weather to AirSim.', true);
      showMapAlert(err.message || 'Failed to sync weather to AirSim.', 'error');
    }
  }

  function onWeatherSliderInput() {
    const payload = getWeatherPayload();
    document.getElementById('rainSliderValue').innerText = `${payload.rain}%`;
    document.getElementById('windSliderValue').innerText = `${payload.wind}%`;
    document.getElementById('snowSliderValue').innerText = `${payload.snow}%`;
    document.getElementById('rainValue').innerText = `${payload.rain}%`;
    document.getElementById('windValue').innerText = `${payload.wind}%`;
    document.getElementById('snowValue').innerText = `${payload.snow}%`;
    if (weatherPushTimer) clearTimeout(weatherPushTimer);
    weatherPushTimer = setTimeout(() => pushWeatherToServer(true), 150);
  }

  async function sendDroneToDestination(lat, lon, label) {
    updateTargetMarker(lat, lon);
    updateStatus('flying', 'Navigating...');
    setMessage(`Sending ${label} ${lat.toFixed(5)}, ${lon.toFixed(5)}...`);
    try {
      const data = await fetchJson('/goto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat, lon })
      });
      navigationStartTime = Date.now();
      navigationStartDistance = 0;
      setMessage(data.message || 'Navigation started.');
      if (Array.isArray(data.warnings) && data.warnings.length) {
        showMapAlert(data.warnings[0], 'warning');
      } else if (data.rerouted) {
        showMapAlert('Route recalculated to avoid restricted airspace.', 'warning');
      } else {
        showMapAlert(data.message || 'Navigation started.', 'success', 'Mission Started');
      }
    } catch (err) {
      updateStatus('error', 'Navigation Error');
      resetNavigationProgressUI();
      setMessage(err.message, true);
      showMapAlert(err.message || 'Navigation blocked.', 'error');
    }
  }

  async function submitAddress() {
    const address = window.prompt('Enter the address to send the drone to:');
    if (!address) return;
    updateStatus('flying', 'Searching...');
    setMessage(`Looking up "${address}"...`);
    try {
      const response = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(address)}`, { headers: { 'Accept': 'application/json' } });
      if (!response.ok) throw new Error(`Address lookup failed with ${response.status}`);
      const results = await response.json();
      if (!Array.isArray(results) || results.length === 0) throw new Error('Address not found. Try a more specific address.');
      const match = results[0];
      const lat = Number(match.lat);
      const lon = Number(match.lon);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) throw new Error('Address lookup returned invalid coordinates.');
      map.setView([lat, lon], 16);
      await sendDroneToDestination(lat, lon, 'address');
    } catch (err) {
      updateStatus('error', 'Lookup Error');
      setMessage(err.message || 'Could not find that address.', true);
    }
  }

  function promptForAddress() { submitAddress(); }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    let data = {};
    try { data = await response.json(); } catch (err) { data = { message: 'Server returned a non-JSON response.' }; }
    if (!response.ok) throw new Error(data.message || `Request failed with ${response.status}`);
    return data;
  }

  async function startDrone() {
    resetNavigationProgressUI();
    updateStatus('taking_off', 'Taking Off...');
    setMessage('Sending takeoff command to AirSim...');
    try {
      const data = await fetchJson('/start');
      updateStatus('idle', 'Ready to Fly');
      setMessage(data.message || 'Takeoff complete.');
      showMapAlert(data.message || 'Takeoff complete.', 'success', 'Drone Ready');
    } catch (err) {
      updateStatus('error', 'Takeoff Failed');
      setMessage(err.message, true);
      showMapAlert(err.message || 'Takeoff failed.', 'error');
    }
  }

  map.on('click', function(e) {
    const lat = e.latlng.lat;
    const lon = e.latlng.lng;
    if (activeNoFlyMode) {
      if (activeNoFlyMode === 'polygon') {
        pendingNoFlyPoints.push({ lat, lon });
        if (pendingNoFlyPreview) map.removeLayer(pendingNoFlyPreview);
        if (pendingNoFlyPoints.length >= 2) {
          const previewFactory = pendingNoFlyPoints.length >= 3 ? L.polygon : L.polyline;
          pendingNoFlyPreview = previewFactory(
            pendingNoFlyPoints.map(point => [point.lat, point.lon]),
            {
              color: '#ef476f',
              weight: 2,
              fillColor: '#ef476f',
              fillOpacity: pendingNoFlyPoints.length >= 3 ? 0.08 : 0,
              dashArray: '5 6'
            }
          ).addTo(map);
        }
        document.getElementById('noFlyHint').innerText = `Polygon point ${pendingNoFlyPoints.length} captured. Add more points or click Finish Polygon.`;
        return;
      }
    }
    if (activeMapMode === 'green' || activeMapMode === 'blue' || activeMapMode === 'garbage') {
      placeWasteBin(activeMapMode, lat, lon).catch(err => setMessage(err.message || 'Failed to place waste bin.', true));
      return;
    }
    if (activeMapMode === 'zone') {
      if (!pendingZoneCorner) {
        pendingZoneCorner = { lat, lon };
        document.getElementById('toolHint').innerText = 'Zone start captured. Click the opposite corner to finish the dispatch area.';
      } else {
        const zone = { south: Math.min(pendingZoneCorner.lat, lat), north: Math.max(pendingZoneCorner.lat, lat), west: Math.min(pendingZoneCorner.lon, lon), east: Math.max(pendingZoneCorner.lon, lon) };
        pendingZoneCorner = null;
        submitZone(zone).catch(err => setMessage(err.message || 'Failed to summarize selected zone.', true));
      }
      return;
    }
    sendDroneToDestination(lat, lon, 'destination');
  });

  function updateStatus(status, message) {
    const statusCard = document.getElementById('statusCard');
    statusCard.className = 'status-card status-' + status;
    statusCard.innerHTML = `<div class="status-label">Flight Status</div><div class="status-text">${message}</div>`;
  }

  function getStatusMessage(status) {
    const messages = { idle: 'Ready to Fly', flying: 'En Route...', rerouting: 'Rerouting...', surveying: 'Surveying Zone...', taking_off: 'Taking Off...', arrived: 'Arrived!', error: 'Navigation Error' };
    return messages[status] || status;
  }

  setInterval(() => {
    fetch('/position')
      .then(async res => {
        let data = {};
        try { data = await res.json(); } catch (err) { data = { message: 'Server returned a non-JSON response.' }; }
        if (!res.ok) throw new Error(data.message || `Request failed with ${res.status}`);
        return data;
      })
      .then(data => {
        if (typeof data.lat !== 'number' || typeof data.lon !== 'number') throw new Error('Position endpoint did not return valid coordinates.');
        const previousStatus = lastFlightStatus;
        lastFlightStatus = data.status;
        updateFeedbackLoop(data.feedback, data.weather);
        if (data.demo) updateSpeedControls(data.demo.speed_multiplier);
        if (data.no_fly_zones) renderNoFlyZones(data.no_fly_zones);
        if (data.flight_plan?.warnings?.length && (data.status === 'flying' || data.status === 'rerouting' || data.status === 'surveying')) {
          setMessage(data.flight_plan.warnings[0]);
          showMapAlert(data.flight_plan.warnings[0], 'warning');
        }
        if (data.dispatch) {
          if (data.dispatch.last_zone) { selectedZone = data.dispatch.last_zone; drawZoneRectangle(data.dispatch.last_zone); }
          updateDispatchSummary(data.dispatch.last_summary || null, data.dispatch.mission || null);
        }
        droneMarker.setLatLng([data.lat, data.lon]);
        updateDroneMarkerDetails(data);
        map.setView([data.lat, data.lon], map.getZoom());
        updateStatus(data.status, getStatusMessage(data.status));
        setMessage(data.message || getStatusMessage(data.status), data.status === 'error');
        if (data.status === 'arrived' && previousStatus !== 'arrived') {
          triggerCelebration(data.message || 'Mission complete!');
          showMapAlert(data.message || 'Mission complete!', 'success', 'Mission Complete');
        }
        if (data.status === 'error' && previousStatus !== 'error') {
          showMapAlert(data.message || 'Navigation error.', 'error');
        }
        document.getElementById('curLat').innerText = data.lat.toFixed(4);
        document.getElementById('curLon').innerText = data.lon.toFixed(4);
        document.getElementById('curAlt').innerText = Math.abs(data.z).toFixed(1) + 'm';
        if ((data.status === 'flying' || data.status === 'rerouting' || data.status === 'surveying') && data.distance_to_goal !== undefined) {
          document.getElementById('progressSection').style.display = 'block';
          const distance = data.distance_to_goal || 0;
          const distanceKm = distance / 1000;
          const progress = data.progress_percent || 0;
          document.getElementById('progressFill').style.width = progress + '%';
          document.getElementById('progressPercent').innerText = Math.round(progress) + '%';
          document.getElementById('distanceValue').innerText = distanceKm.toFixed(2);
          document.getElementById('timeElapsed').innerText = 'Time: ' + Math.floor((Date.now() - navigationStartTime) / 1000) + 's';
        } else if (data.status === 'arrived') {
          document.getElementById('progressSection').style.display = 'block';
          document.getElementById('progressFill').style.width = '100%';
          document.getElementById('progressPercent').innerText = '100%';
          document.getElementById('distanceValue').innerText = '0.00';
        } else if (data.status === 'idle' || data.status === 'error') {
          document.getElementById('progressSection').style.display = 'none';
        }
      })
      .catch(err => {
        if (suppressStatusPollingError) return;
        updateStatus('error', 'Status Error');
        setMessage(err.message || 'Could not reach Flask status endpoint.', true);
        showMapAlert(err.message || 'Could not reach Flask status endpoint.', 'error');
      });
  }, 1000);

  refreshDispatchData().catch(err => console.error(err));
  refreshNoFlyZones().catch(err => console.error(err));
  fetchJson('/speed').then(data => updateSpeedControls(data.speed_multiplier)).catch(() => {});
