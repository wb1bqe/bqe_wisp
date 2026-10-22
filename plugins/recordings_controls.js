(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const rows = [...document.querySelectorAll('#recordingsBody tr')];
  const dialog = byId('satelliteFilterDialog');
  const sources = [...new Set(rows.map(row => row.dataset.source))].sort((a, b) => a.localeCompare(b));
  let allowed = new Set(sources);
  let running = false;
  let timer;
  let lastState = '';
  let revision = 0;
  const selected = () => rows.filter(row => !row.hidden && row.querySelector('.morse-checkbox').checked);
  try {
    const saved = JSON.parse(sessionStorage.getItem('recordingsSatelliteFilter'));
    if (Array.isArray(saved)) allowed = new Set(saved);
  } catch (_) { /* Storage is optional. */ }

  window.updateRecordingDecodeSelection = () => {
    const disabled = running || morseDecodeRunning || !selected().length;
    byId('sstvDecodeButton').disabled = disabled;
    byId('aprsDecodeButton').disabled = disabled;
    byId('speechDecodeButton').disabled = disabled;
  };
  function applyFilter() {
    let count = 0;
    for (const row of rows) {
      row.hidden = !allowed.has(row.dataset.source);
      if (row.hidden) row.querySelector('.morse-checkbox').checked = false;
      else count++;
    }
    byId('recordingFilterStatus').textContent = `${count} of ${rows.length} recordings shown`;
    byId('noFilteredRecordings').hidden = !rows.length || count > 0;
    updateMorseSelectionState();
  }
  byId('satelliteFilterButton').addEventListener('click', () => {
    const choices = byId('satelliteFilterChoices');
    choices.replaceChildren();
    for (const name of sources) {
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = name;
      checkbox.checked = allowed.has(name);
      label.append(checkbox, document.createTextNode(`${name} (${rows.filter(row => row.dataset.source === name).length})`));
      choices.append(label);
    }
    dialog.showModal();
  });
  for (const [id, checked] of [['filterSelectAll', true], ['filterSelectNone', false]]) {
    byId(id).addEventListener('click', () => {
      dialog.querySelectorAll('input').forEach(input => { input.checked = checked; });
    });
  }
  byId('filterCancel').addEventListener('click', () => dialog.close());
  byId('filterApply').addEventListener('click', () => {
    allowed = new Set([...dialog.querySelectorAll('input:checked')].map(input => input.value));
    try { sessionStorage.setItem('recordingsSatelliteFilter', JSON.stringify([...allowed])); } catch (_) {}
    applyFilter();
    dialog.close();
  });

  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {cache: 'no-store'} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || `Request failed (${response.status})`);
    return result;
  }
  function render(state) {
    running = state.running;
    byId('cancelRecordingDecode').hidden = !running;
    window.updateRecordingDecodeSelection();
    const key = JSON.stringify(state);
    if (key === lastState) return;
    lastState = key;
    if (!state.items.length) return;
    const label = state.decoder === 'speech' ? 'Speech transcription' : state.decoder.toUpperCase();
    const finished = state.items.filter(item => ['completed', 'error', 'cancelled'].includes(item.state)).length;
    const errors = state.items.filter(item => item.state === 'error').length;
    const cancelled = state.items.filter(item => item.state === 'cancelled').length;
    byId('recordingDecodeStatus').textContent = `${label}: ${finished} of ${state.items.length} finished` +
      (running ? ' — decoding…' : ` — ${errors} failed, ${cancelled} cancelled.`);
    for (const item of state.items) {
      const row = rows.find(row => row.querySelector('.morse-checkbox').dataset.name === item.name);
      if (!row) continue;
      const cell = row.querySelector('[data-recording-decode-result]');
      cell.replaceChildren();
      cell.textContent = `${label}: ` + (item.state === 'completed' ?
        `${item.count} ${{sstv: 'image(s) decoded', aprs: 'packet(s) decoded', speech: 'speech segment(s) transcribed'}[state.decoder]}` : item.state);
      if (item.message) cell.append(document.createTextNode(` — ${item.message}`));
      if (item.state === 'running' && Number.isFinite(item.percent)) {
        cell.append(document.createTextNode(` (${item.percent}%)`));
      }
      if (item.result) {
        if (state.decoder === 'speech') {
          const saved = row.querySelector('.transcript-open');
          if (saved) saved.hidden = false;
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = 'View transcript';
          button.addEventListener('click', () => window.openRecordingTranscript(item.name));
          cell.append(document.createElement('br'), button);
          continue;
        }
        if (state.decoder === 'sstv') {
          for (const image of item.result.recent || []) {
            if (!image.filename) continue;
            const link = document.createElement('a');
            link.href = '/sstv-image?name=' + encodeURIComponent(image.filename);
            link.target = '_blank';
            link.rel = 'noopener';
            link.textContent = image.filename;
            cell.append(document.createElement('br'), link);
          }
        }
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = 'View results';
        const pre = document.createElement('pre');
        pre.textContent = JSON.stringify(item.result, null, 2);
        details.append(summary, pre);
        cell.append(details);
      }
    }
  }
  async function poll() {
    clearTimeout(timer);
    const version = revision;
    try {
      const state = await request('/api/recordings/decode');
      if (version === revision) render(state);
    }
    catch (error) { byId('recordingDecodeStatus').textContent = `Cannot check decoding: ${error.message}`; }
    timer = setTimeout(poll, running ? 1000 : 5000);
  }
  async function decode(decoder) {
    const names = selected().map(row => row.querySelector('.morse-checkbox').dataset.name);
    if (!names.length || running) return;
    revision++;
    running = true;
    window.updateRecordingDecodeSelection();
    try { render(await request('/api/recordings/decode', {decoder, names, ...(decoder === 'speech' ? {language: byId('speechLanguage').value} : {})})); }
    catch (error) {
      running = false;
      window.updateRecordingDecodeSelection();
      byId('recordingDecodeStatus').textContent = error.message;
      return;
    }
    poll();
  }
  byId('sstvDecodeButton').addEventListener('click', () => decode('sstv'));
  byId('aprsDecodeButton').addEventListener('click', () => decode('aprs'));
  byId('speechDecodeButton').addEventListener('click', () => decode('speech'));
  byId('cancelRecordingDecode').addEventListener('click', async () => {
    revision++;
    try { render(await request('/api/recordings/decode/cancel', {})); }
    catch (error) { byId('recordingDecodeStatus').textContent = error.message; }
  });
  applyFilter();
  poll();
})();
