/* Shared by decoder control pages. HTTP/application errors do not mean exit. */
window.BqePluginPage = (() => {
  let failures = 0, firstFailure = null, stopped = false;
  async function request(path, init) {
    if (path !== '/status') return window.fetch(path, init);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    try {
      const response = await window.fetch(path, {...init, signal: controller.signal, cache: 'no-store'});
      failures = 0;
      firstFailure = null;
      return response;
    } catch (error) {
      if (!stopped) {
        const now = Date.now();
        if (firstFailure === null) firstFailure = now;
        failures++;
        if (failures >= 3 && now - firstFailure >= 5000) {
          stopped = true;
          // Let the page's current refresh finish before unloading its DOM.
          window.setTimeout(() => {
            try { window.close(); } catch (_) { /* Browser policy may forbid it. */ }
            // External browser launches are not always script-closable. Unload
            // the decoder anyway, releasing images, data and polling timers.
            window.setTimeout(() => window.location.replace('about:blank'), 100);
          }, 0);
        }
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }
  function poll(refresh, delay) {
    async function tick() {
      if (stopped) return;
      try { await refresh(); }
      finally { if (!stopped) window.setTimeout(tick, delay); }
    }
    tick();
  }
  return {fetch: request, poll};
})();
