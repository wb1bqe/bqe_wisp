/* Saved telemetry viewer. Opening it never starts a decoder or touches a radio. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const modal = $('telemetryModal');
  if (!modal) return;
  let satellite = null, packets = [], timer = null, request = null, returnFocus = null;
  let satellites = [], catalogRequest = null, lastSatelliteId = null, rendered = null;
  const text = (id, value) => { $(id).textContent = value; };
  const time = value => {
    if (!value) return 'Time unavailable';
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString();
  };
  async function json(url, signal) {
    const response = await fetch(url, {cache: 'no-store', signal});
    const result = await response.json();
    if (!response.ok || !result.ok) throw Error(result.message || 'Could not load telemetry.');
    return result;
  }
  async function loadSatellites() {
    if (catalogRequest) catalogRequest.abort();
    const controller = new AbortController(); catalogRequest = controller;
    const select = $('telemetrySatelliteSelect');
    select.disabled = true;
    const loading = document.createElement('option'); loading.textContent = 'Loading satellites...';
    select.replaceChildren(loading);
    text('telemetryMessage', 'Loading satellites...');
    try {
      const result = await json('/api/telemetry/satellites', controller.signal);
      if (controller.signal.aborted) return;
      satellites = result.satellites;
      const options = satellites.map(entry => {
        const option = document.createElement('option'); option.value = entry.id;
        option.textContent = entry.nickname + (entry.name !== entry.nickname ? ' — ' + entry.name : '');
        if (entry.has_recent_telemetry === true) {
          option.classList.add('telemetry-recent-data');
          option.textContent += ' (received within 24 hours)';
        } else if (entry.has_telemetry === false) {
          option.classList.add('telemetry-no-data');
          option.textContent += ' (no saved telemetry)';
        }
        return option;
      });
      if (!options.length) {
        const empty = document.createElement('option'); empty.textContent = 'No telemetry decoders enabled';
        select.replaceChildren(empty);
        text('telemetryMessage', 'No satellites have telemetry decoders enabled.');
        return;
      }
      select.replaceChildren(...options); select.disabled = false;
      const entry = satellites.find(item => item.id === lastSatelliteId) || satellites[0];
      select.value = entry.id;
      selectSatellite(entry);
    } catch (error) {
      if (controller.signal.aborted) return;
      const empty = document.createElement('option'); empty.textContent = 'Unable to load satellites';
      select.replaceChildren(empty);
      text('telemetryMessage', error.message + ' Use Refresh to retry.');
    } finally {
      if (catalogRequest === controller) catalogRequest = null;
    }
  }
  function clearPacket() {
    rendered = null;
    for (const id of ['telemetryTime', 'telemetrySource', 'telemetryFormat']) text(id, '—');
    $('telemetryFields').replaceChildren(); text('telemetryRaw', ''); text('telemetryLogPath', '');
  }
  function fieldChart(selected, entry, name) {
    const container = document.createElement('div'); container.className = 'telemetry-trend';
    const chart = BQETelemetryCharts.geometry(BQETelemetryCharts.series(packets, selected, entry, name));
    if (!chart) {
      container.textContent = 'No numeric history'; container.classList.add('telemetry-trend-empty');
      return container;
    }
    const number = value => value.toLocaleString(undefined, {maximumSignificantDigits: 17});
    const summary = document.createElement('div'); summary.className = 'telemetry-trend-range';
    summary.textContent = `Range: ${number(chart.min)} – ${number(chart.max)} · ${chart.count} ${chart.count === 1 ? 'sample' : 'samples'}`;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 240 52'); svg.setAttribute('role', 'img');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-label', `${name}: ${chart.count} samples, minimum ${chart.min}, maximum ${chart.max}, decoded from ${time(new Date(chart.start).toISOString())} to ${time(new Date(chart.end).toISOString())}. Highlighted dot is the selected packet.`);
    const shape = (tag, attrs) => {
      const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
      svg.append(node); return node;
    };
    shape('path', {d: 'M6,48 L234,48', class: 'telemetry-trend-axis'});
    shape('path', {d: chart.path, class: 'telemetry-trend-line'});
    // Draw selected samples last so they remain visible at overlapping times.
    for (const point of [...chart.dots].sort((a,b) => Number(a.id === selected.id) - Number(b.id === selected.id))) {
      const dot = shape('circle', {cx: point.x, cy: point.y, r: point.id === selected.id ? 3.5 : 2.5,
        class: point.id === selected.id ? 'telemetry-trend-selected' : 'telemetry-trend-dot'});
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = `${time(new Date(point.timestamp).toISOString())}: ${point.value}` + (point.id === selected.id ? ' (selected packet)' : '');
      dot.append(title);
    }
    const axis = document.createElement('div'); axis.className = 'telemetry-trend-times';
    for (const stamp of [chart.start, chart.end]) {
      const label = document.createElement('span'), date = new Date(stamp);
      label.textContent = date.toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit'});
      label.title = date.toISOString(); axis.append(label);
    }
    container.append(summary, svg, axis); return container;
  }
  function renderPacket() {
    const id = $('telemetryPacketSelect').value;
    const record = id === 'latest' ? packets[0] : packets.find(item => item.id === id);
    if (!record) { clearPacket(); return; }
    const renderKey = record.id + ':' + packets.map(item => item.id).join(',');
    if (rendered === renderKey) return;
    rendered = renderKey;
    const packet = record.packet;
    text('telemetryTime', time(packet.decoded_at));
    text('telemetrySource', packet.source || packet.input_source || 'Unknown');
    text('telemetryFormat', [packet.protocol, packet.baud ? packet.baud + ' baud' : '', packet.validation].filter(Boolean).join(' · '));
    const sections = [];
    for (const entry of Array.isArray(packet.telemetry) ? packet.telemetry : []) {
      if (!entry || typeof entry !== 'object') continue;
      const section = document.createElement('section'), heading = document.createElement('h3');
      heading.textContent = (entry.name || entry.record_type || 'Telemetry fields') +
        (entry.parse_status ? ' · ' + entry.parse_status : '');
      section.append(heading);
      const fields = entry.fields && typeof entry.fields === 'object' ? Object.entries(entry.fields) : [];
      const grid = document.createElement('div'); grid.className = 'config-fields';
      for (const [name, value] of fields) {
        const label = document.createElement('label'), input = document.createElement('input');
        input.id = 'tlm-field-' + sections.length + '-' + grid.children.length;
        input.type = 'text'; input.readOnly = true;
        label.htmlFor = input.id; label.textContent = name.replaceAll('_', ' ');
        input.value = value == null ? 'Unavailable' : typeof value === 'object' ? JSON.stringify(value) : String(value);
        input.title = input.value; grid.append(label, input, fieldChart(record, entry, name));
      }
      section.append(grid);
      if (!fields.length) {
        const message = document.createElement('p');
        message.textContent = 'No interpreted fields for this record. See the raw packet below.'; section.append(message);
      }
      sections.push(section);
    }
    if (!sections.length) {
      const raw = document.createElement('p');
      raw.textContent = 'Raw packet received. This decoder has no interpreted telemetry fields for this packet.';
      sections.push(raw);
    }
    $('telemetryFields').replaceChildren(...sections);
    text('telemetryRaw', JSON.stringify(packet, null, 2));
    text('telemetryLogPath', 'Saved in: ' + record.log_file);
  }
  async function refresh() {
    if (!satellite || request) return;
    const current = satellite, controller = new AbortController(); request = controller;
    $('telemetryRefresh').disabled = true;
    try {
      const result = await json('/api/telemetry?satellite=' + encodeURIComponent(current.id), controller.signal);
      if (satellite !== current || controller.signal.aborted) return;
      const select = $('telemetryPacketSelect'), selected = select.value;
      const retained = packets.find(item => item.id === selected);
      const availableCount = result.packets.length;
      packets = [...result.packets];
      if (retained && !packets.some(item => item.id === retained.id)) packets.push(retained);
      const latest = document.createElement('option'); latest.value = 'latest'; latest.textContent = 'Latest packet (auto-update)';
      const options = packets.map(item => {
        const option = document.createElement('option'); option.value = item.id;
        option.textContent = time(item.packet.decoded_at) + ' · ' + (item.packet.source || item.packet.protocol || 'Packet');
        return option;
      });
      select.replaceChildren(latest, ...options);
      select.value = packets.some(item => item.id === selected) ? selected : 'latest';
      const message = packets.length ?
        `${availableCount} recent ${availableCount === 1 ? 'packet' : 'packets'} available. Saved telemetry refreshes every 3 seconds.` :
        'No decoded telemetry saved for this satellite yet. Its configured decoder will save packets when received during a pass.';
      text('telemetryMessage', [message, ...result.warnings].join(' '));
      text('telemetryScope', result.scope + ' Graphs use this loaded history, with decoded-at time on the horizontal axis and an independent value scale per field. Hover over a dot for its value; the highlighted dot is the selected packet.');
      renderPacket();
      if (!packets.length) text('telemetryLogPath', 'Telemetry folders: ' + result.locations.join(' · '));
    } catch (error) {
      if (error.name !== 'AbortError' && satellite === current) {
        text('telemetryMessage', 'Refresh failed: ' + error.message + (packets.length ? ' Showing previously loaded packets.' : ''));
      }
    } finally {
      if (request === controller) { request = null; $('telemetryRefresh').disabled = false; }
    }
  }
  function close() {
    if (catalogRequest) catalogRequest.abort(); catalogRequest = null;
    satellite = null; clearInterval(timer); timer = null;
    if (request) request.abort(); request = null;
    modal.classList.remove('open'); modal.setAttribute('aria-hidden', 'true');
    if (returnFocus?.isConnected) returnFocus.focus();
  }
  function selectSatellite(entry) {
    if (request) request.abort(); request = null; clearInterval(timer);
    satellite = entry; lastSatelliteId = entry.id; packets = [];
    clearPacket();
    const latest = document.createElement('option'); latest.value = 'latest'; latest.textContent = 'Latest packet (auto-update)';
    $('telemetryPacketSelect').replaceChildren(latest); $('telemetryRawDetails').open = false;
    text('telemetryTitle', entry.nickname + ' — Telemetry');
    text('telemetryDescription', [entry.name, entry.norad ? 'NORAD ' + entry.norad : '',
      entry.frequency ? entry.frequency + ' MHz' : '', entry.decoders.map(d => d.label).join(', ')].filter(Boolean).join(' · '));
    text('telemetryMessage', 'Loading saved telemetry...'); text('telemetryScope', '');
    refresh(); timer = setInterval(refresh, 3000);
  }
  function open() {
    returnFocus = $('telemetryMenuItem');
    satellite = null; packets = []; clearPacket();
    text('telemetryTitle', 'Telemetry'); text('telemetryDescription', ''); text('telemetryScope', '');
    $('telemetryPacketSelect').replaceChildren(); $('telemetryRawDetails').open = false;
    $('telemetryRefresh').disabled = false;
    modal.classList.add('open'); modal.setAttribute('aria-hidden', 'false'); $('telemetryClose').focus();
    loadSatellites();
  }
  $('telemetrySatelliteSelect').addEventListener('change', () => {
    const entry = satellites.find(item => item.id === $('telemetrySatelliteSelect').value);
    if (entry) selectSatellite(entry);
  });
  $('telemetryClose').addEventListener('click', close);
  $('telemetryDone').addEventListener('click', close);
  $('telemetryRefresh').addEventListener('click', () => satellite ? refresh() : loadSatellites());
  $('telemetryPacketSelect').addEventListener('change', renderPacket);
  modal.addEventListener('click', event => { if (event.target === modal) close(); });
  document.addEventListener('keydown', event => {
    if (!modal.classList.contains('open')) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close(); }
    if (event.key === 'Tab') {
      const controls = [...modal.querySelectorAll('button:not(:disabled), select:not(:disabled), input, summary')];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  }, true);
  $('telemetryMenuItem').addEventListener('click', open);
})();
