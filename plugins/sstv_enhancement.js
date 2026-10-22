/* Offline gallery enhancement; selection and originals stay intact. */
(() => {
  let boxes = [...document.querySelectorAll('.enhance-select')];
  const deleteButton = document.getElementById('deleteImagesButton');
  const button = document.getElementById('enhanceButton');
  const cancel = document.getElementById('enhanceCancel');
  const status = document.getElementById('enhanceStatus');
  const results = document.getElementById('enhanceResults');
  let running = false, deleting = false, timer = null;
  function selection() {
    const count = boxes.filter(box => box.checked).length;
    button.disabled = running || deleting || count < 2 || count > 24;
    deleteButton.disabled = running || deleting || count === 0;
    if (!running) status.textContent = `${count} selected. Select 2–24 images for enhancement.`;
  }
  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || 'Gallery request failed');
    return data;
  }
  function render(data) {
    running = data.state === 'running';
    cancel.hidden = !running;
    selection();
    if (data.state !== 'idle') status.textContent = data.message || `Analyzed ${data.completed || 0} of ${data.total || '?'} images…`;
    results.replaceChildren();
    for (const output of data.outputs || []) {
      const row = document.createElement('li'), link = document.createElement('a');
      link.href = '/sstv-image?name=' + encodeURIComponent(output.filename);
      link.target = '_blank'; link.rel = 'noopener';
      link.textContent = `${output.filename} — ${output.sources.length} images combined`;
      row.append(link);
      if (output.confidence_kind === 'estimated') row.append(' (includes estimated image quality)');
      if (output.warning) row.append(' — ' + output.warning);
      results.append(row);
    }
    for (const item of data.skipped || []) {
      const row = document.createElement('li');
      row.textContent = `${item.filename}: ${item.reason}`;
      results.append(row);
    }
    clearTimeout(timer);
    if (running) timer = setTimeout(poll, 1000);
  }
  async function poll() {
    try { render(await request('/api/sstv/enhancement')); }
    catch (error) {
      status.textContent = error.message;
      if (running) timer = setTimeout(poll, 3000);
    }
  }
  for (const box of boxes) box.addEventListener('change', selection);
  document.getElementById('enhanceSelectAll').addEventListener('click', () => {
    const checked = !boxes.every(box => box.checked);
    boxes.forEach(box => { box.checked = checked; }); selection();
  });
  button.addEventListener('click', async () => {
    const names = boxes.filter(box => box.checked).map(box => box.dataset.name);
    running = true; selection(); status.textContent = 'Starting enhancement…';
    try { render(await request('/api/sstv/enhancement', {names})); }
    catch (error) { running = false; selection(); status.textContent = error.message; }
  });
  deleteButton.addEventListener('click', async () => {
    const names = boxes.filter(box => box.checked).map(box => box.dataset.name);
    if (!names.length || running || deleting) return;
    deleting = true; selection(); status.textContent = 'Deleting selected images…';
    try {
      const data = await request('/api/sstv/delete', {names});
      const deleted = new Set(data.deleted || []);
      for (const box of boxes) {
        if (deleted.has(box.dataset.name)) box.closest('.image-card').remove();
      }
      boxes = boxes.filter(box => !deleted.has(box.dataset.name));
      deleting = false; selection();
      status.textContent = data.message + (data.errors || []).map(item => ` ${item.name}: ${item.message}`).join('');
    } catch (error) {
      deleting = false; selection(); status.textContent = error.message;
    }
  });
  cancel.addEventListener('click', async () => {
    try { render(await request('/api/sstv/enhancement/cancel', {})); }
    catch (error) { status.textContent = error.message; }
  });
  selection(); poll();
})();
