/* Each page owns its fetch connection and playback context. */
(() => {
  const button = document.getElementById('streamAudioMenuItem');
  let session = null;
  const message = text => {
    const node = document.getElementById('message');
    if (node) node.textContent = text;
  };
  function stop() {
    const old = session;
    session = null;
    button.textContent = 'Stream Audio';
    button.setAttribute('aria-checked', 'false');
    if (old) {
      old.abort.abort();
      old.context.close().catch(() => {});
    }
  }
  button.addEventListener('click', async () => {
    if (session) { stop(); message('Audio streaming stopped.'); return; }
    let current;
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) throw new Error('This browser does not support Web Audio.');
      current = {context: new AudioContext(), abort: new AbortController()};
      session = current;
      button.textContent = 'Stop Streaming';
      button.setAttribute('aria-checked', 'true');
      // Resume immediately within the user's click (also works over LAN HTTP).
      await current.context.resume();
      if (session !== current) return;
      message('Connecting to sound-card audio...');
      const response = await fetch('/audio-stream/pcm', {
        headers: {'X-BQE-Audio': '1'}, cache: 'no-store', signal: current.abort.signal
      });
      if (!response.ok) throw new Error(await response.text());
      if (session !== current) return;
      const rate = Number(response.headers.get('X-Audio-Sample-Rate'));
      if (!Number.isFinite(rate) || rate < 8000 || rate > 96000) throw new Error('Invalid audio sample rate.');
      const reader = response.body.getReader();
      const ctx = current.context;
      let pending = new Uint8Array(0), next = ctx.currentTime + .12;
      const blockBytes = Math.floor(rate * .04) * 2;
      message('Streaming sound-card audio. Choose Audio / Stop Streaming to stop.');
      while (session === current) {
        const {value, done} = await reader.read();
        if (done) throw new Error('Audio stream ended. Check the sound device and start streaming again.');
        if (session !== current) break;
        const joined = new Uint8Array(pending.length + value.length);
        joined.set(pending); joined.set(value, pending.length);
        let offset = 0;
        while (offset + blockBytes <= joined.length) {
          // Discard backlogged packets rather than building increasing delay.
          if (ctx.state === 'running' && next < ctx.currentTime + .5) {
            const buffer = ctx.createBuffer(1, blockBytes / 2, rate);
            const samples = buffer.getChannelData(0);
            const view = new DataView(joined.buffer, offset, blockBytes);
            for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
            const source = ctx.createBufferSource();
            source.buffer = buffer; source.connect(ctx.destination);
            source.onended = () => source.disconnect();
            next = Math.max(next, ctx.currentTime + .03);
            source.start(next); next += buffer.duration;
          }
          offset += blockBytes;
        }
        pending = joined.slice(offset);
      }
    } catch (error) {
      if (session === current) {
        stop();
        message('Audio streaming: ' + error.message);
      }
    }
  });
  window.addEventListener('pagehide', stop);
  window.bqeStopAudioStreaming = stop;
})();
