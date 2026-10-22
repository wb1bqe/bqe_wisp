/* Read-only spot history; never starts a receiver or queries a callsign service. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const modal = $('aprsSpotsModal');
  if (!modal?.dataset.standalone) {
    let viewer = null;
    $('aprsSpotsMenuItem')?.addEventListener('click', () => {
      if (viewer && !viewer.closed) { viewer.focus(); return; }
      viewer = window.open('/aprs-spots', 'bqeWispAprsSpots', 'width=1250,height=900,resizable=yes,scrollbars=yes');
      if (!viewer) {
        const message = $('message');
        if (message) message.textContent = 'The APRS Spots window was blocked. Allow popups for this site and try again.';
      }
    });
    return;
  }
  let opened = false, request = null, timer = null, spots = [], selected = null;
  let map = null, markers = null, plotted = [], fitPending = true, returnFocus = null;
  let warnings = [];
  let rendered = null;
  const positioned = spot => Number.isFinite(spot.latitude) && Number.isFinite(spot.longitude);
  const date = value => {
    if (!value) return 'Unknown';
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString();
  };
  function detail(spot, center = false) {
    selected = spot.id;
    $('aprsSpotsDetail').textContent = [
      `${spot.name} · originator ${spot.callsign}${spot.active ? '' : ' · removed object'}`,
      `Type: ${spot.format || 'Unknown'} · Destination: ${spot.destination || 'Unknown'}`,
      positioned(spot) ? `Position: ${spot.latitude.toFixed(5)}, ${spot.longitude.toFixed(5)}` : 'No position in this packet',
      `Decoded: ${date(spot.decoded_at)} · Source: ${spot.input_source || 'Unknown'}`,
      spot.uploaded ? `Upload: sent to APRS-IS at ${date(spot.uploaded_at)}` : 'Upload: not uploaded / no upload recorded',
      Number.isFinite(spot.audio_seconds) ? `Audio offset: ${spot.audio_seconds.toFixed(3)} seconds` : '',
      spot.path.length ? `Path: ${spot.path.join(', ')}` : '',
      spot.comment || '', spot.tnc2 || '', `Log: ${spot.log_file}`,
      spot.packet ? '\nDecoded fields:\n' + JSON.stringify(spot.packet, null, 2) : ''
    ].filter(Boolean).join('\n');
    $('aprsSpotsDetail').dataset.uploaded = String(spot.uploaded === true);
    $('aprsSpotsDetail').dataset.satellitePass = String(spot.satellite_pass === true);
    for (const row of $('aprsSpotsRows').children) row.setAttribute('aria-selected', String(row.dataset.id === selected));
    if (center && map && positioned(spot)) map.setView([spot.latitude, spot.longitude], Math.max(map.getZoom(), 6));
  }
  function fit() {
    if (map && plotted.length) map.fitBounds(plotted, {padding: [24, 24], maxZoom: 10});
  }
  function render() {
    const focusedId = document.activeElement?.closest?.('tr[data-id]')?.dataset.id;
    const search = $('aprsSpotsFilter').value.trim().toLowerCase();
    const visible = spots.filter(spot => [spot.callsign, spot.name, spot.input_source, spot.destination,
      spot.format, spot.comment, spot.tnc2].join(' ').toLowerCase().includes(search));
    $('aprsSpotsRows').replaceChildren(...visible.map(spot => {
      const row = document.createElement('tr'); row.dataset.id = spot.id;
      row.dataset.uploaded = String(spot.uploaded === true);
      row.dataset.satellitePass = String(spot.satellite_pass === true);
      row.title = spot.uploaded ? 'Uploaded to APRS-IS' : 'Not uploaded / no upload recorded';
      row.setAttribute('aria-selected', String(spot.id === selected));
      const time = document.createElement('td'); time.textContent = date(spot.decoded_at); row.append(time);
      const station = document.createElement('td'), button = document.createElement('button');
      button.type = 'button'; button.textContent = spot.name + (spot.active ? '' : ' (removed)');
      button.addEventListener('click', () => detail(spot, true)); station.append(button); row.append(station);
      for (const value of [spot.format || 'Unknown', spot.destination || '',
        positioned(spot) ? `${spot.latitude.toFixed(5)}, ${spot.longitude.toFixed(5)}` : '—',
        spot.input_source || 'Unknown', spot.comment]) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      return row;
    }));
    if (focusedId) {
      const retained = [...$('aprsSpotsRows').children].find(row => row.dataset.id === focusedId);
      if (retained) retained.querySelector('button').focus();
      else $('aprsSpotsRefresh').focus();
    }
    const latest = new Map();
    for (const spot of visible) if (positioned(spot) && !latest.has(spot.key)) latest.set(spot.key, spot);
    plotted = [];
    if (markers) {
      markers.clearLayers();
      for (const spot of latest.values()) {
        if (!spot.active) continue;
        plotted.push([spot.latitude, spot.longitude]);
        const satellite = spot.satellite_pass === true;
        const label = document.createElement('span');
        label.textContent = `${spot.name} · ${satellite ? 'Satellite pass' : 'Preset / other reception'}`;
        const position = [spot.latitude, spot.longitude];
        const marker = satellite ? L.marker(position, {
          icon: L.divIcon({className: 'aprs-map-satellite', iconSize: [36, 36],
            iconAnchor: [18, 18], tooltipAnchor: [0, -18]}),
          title: label.textContent, alt: label.textContent
        }) : L.circleMarker(position, {radius: 7, color: '#1263a0', fillColor: '#1263a0', fillOpacity: .8});
        marker.bindTooltip(label).on('click', () => detail(spot)).addTo(markers);
      }
      if (fitPending && plotted.length) { fit(); fitPending = false; }
    }
    const message = spots.length ? `${visible.length} of ${spots.length} decoded APRS packets. Updates every 3 seconds.` :
      'No APRS packets decoded yet. Live reception and recording decodes will appear here.';
    $('aprsSpotsMessage').textContent = [message, ...warnings, map ? '' : 'Map unavailable; all decoded packets are listed below.'].filter(Boolean).join(' ');
    const current = visible.find(spot => spot.id === selected);
    if (current) detail(current);
    else { selected = null; $('aprsSpotsDetail').textContent = ''; }
  }
  async function refresh() {
    if (!opened || request) return;
    const controller = new AbortController(); request = controller;
    $('aprsSpotsRefresh').disabled = true;
    try {
      const response = await fetch('/api/aprs/spots', {cache: 'no-store', signal: controller.signal});
      const data = await response.json();
      if (!response.ok || !data.ok) throw Error(data.message || 'Could not load APRS spots');
      if (!opened || request !== controller) return;
      spots = data.packets || data.spots; warnings = data.warnings || [];
      $('aprsSpotsScope').textContent = data.scope;
      const signature = JSON.stringify([spots, warnings]);
      if (signature !== rendered) { render(); rendered = signature; }
    } catch (error) {
      if (opened && request === controller && error.name !== 'AbortError') {
        rendered = null;
        $('aprsSpotsMessage').textContent = `Refresh failed: ${error.message}` + (spots.length ? ' Showing previously loaded packets.' : '');
      }
    } finally {
      if (request === controller) { request = null; $('aprsSpotsRefresh').disabled = false; }
    }
  }
  function close() {
    opened = false; clearInterval(timer); timer = null;
    if (request) request.abort(); request = null;
    modal.classList.remove('open'); modal.setAttribute('aria-hidden', 'true');
    if (returnFocus?.isConnected) returnFocus.focus();
    window.close();
  }
  function open() {
    if (opened) return;
    opened = true; returnFocus = $('aprsSpotsMenuItem'); fitPending = true; rendered = null;
    modal.classList.add('open'); modal.setAttribute('aria-hidden', 'false');
    $('aprsSpotsClose').focus(); $('aprsSpotsMessage').textContent = 'Loading received APRS spots...';
    if (!map && typeof L !== 'undefined') {
      map = L.map('aprsSpotsMap').setView([20, 0], 2);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, attribution: '© OpenStreetMap contributors'
      }).addTo(map);
      markers = L.layerGroup().addTo(map);
    }
    if (map) requestAnimationFrame(() => map.invalidateSize());
    refresh(); timer = setInterval(refresh, 3000);
  }
  $('aprsSpotsClose').addEventListener('click', close);
  $('aprsSpotsDone').addEventListener('click', close);
  $('aprsSpotsRefresh').addEventListener('click', refresh);
  $('aprsSpotsFit').addEventListener('click', fit);
  $('aprsSpotsFilter').addEventListener('input', () => { fitPending = true; render(); });
  modal.addEventListener('click', event => { if (event.target === modal) close(); });
  document.addEventListener('keydown', event => {
    if (modal.dataset.standalone) return;
    if (!opened) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close(); }
    if (event.key === 'Tab') {
      const controls = [...modal.querySelectorAll('button:not(:disabled), input, a[href], [tabindex="0"]')];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  }, true);
  window.addEventListener('pagehide', () => { opened = false; clearInterval(timer); request?.abort(); });
  open();
})();
