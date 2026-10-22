/* Saved, timestamped transcripts; all text is displayed as text, never markup. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const languageButtons = [...document.querySelectorAll('[data-speech-language]')];
  languageButtons.forEach(button => button.addEventListener('click', () => {
    byId('speechLanguage').value = button.dataset.speechLanguage;
    languageButtons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  }));
  const dialog = byId('transcriptDialog');
  const player = byId('transcriptPlayer');
  const status = byId('transcriptStatus');
  let generation = 0;
  const time = seconds => {
    const whole = Math.floor(seconds);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor(whole / 60) % 60;
    return `${hours ? String(hours).padStart(2, '0') + ':' : ''}${String(minutes).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}`;
  };
  function close() {
    generation++;
    player.pause();
  }
  dialog.addEventListener('close', close);
  byId('transcriptClose').addEventListener('click', () => dialog.close());
  player.addEventListener('play', () => byId('recordingPlayer').pause());
  window.openRecordingTranscript = async name => {
    const current = ++generation;
    player.pause();
    player.removeAttribute('src');
    player.load();
    byId('transcriptTitle').textContent = `Speech transcript — ${name}`;
    status.textContent = 'Loading saved transcript…';
    byId('transcriptSegments').replaceChildren();
    byId('transcriptDownload').hidden = true;
    if (!dialog.open) dialog.showModal();
    try {
      const url = '/recording-transcript?name=' + encodeURIComponent(name);
      const response = await fetch(url, {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || 'Transcript unavailable');
      if (current !== generation) return;
      player.src = '/recording-file?name=' + encodeURIComponent(name);
      const requested = data.requested_language === 'auto' ? data.language : (data.requested_language || data.language);
      let language = languageButtons.find(button => button.dataset.speechLanguage === requested)?.textContent;
      if (!language) {
        try { language = new Intl.DisplayNames(['en'], {type:'language'}).of(requested); }
        catch (_) { language = requested; }
      }
      if (data.language === 'und') language = 'Undetermined language';
      byId('transcriptTitle').textContent = `${language} transcript — ${name}`;
      status.textContent = `${data.model} · ${language}${data.requested_language === 'auto' ? ' (auto-detected)' : ''} · ${data.segments.length} speech segments · Saved ${new Date(data.created_at).toLocaleString()}`;
      const download = byId('transcriptDownload');
      download.href = url + '&format=txt';
      download.download = name + '.transcript.txt';
      download.hidden = false;
      if (!data.segments.length) byId('transcriptSegments').textContent = 'No speech was transcribed.';
      for (const segment of data.segments) {
        const row = document.createElement('div');
        row.className = 'transcript-segment';
        const timestamp = document.createElement('button');
        timestamp.type = 'button';
        timestamp.textContent = `${time(segment.start)} – ${time(segment.end)}`;
        timestamp.setAttribute('aria-label', `Play audio at ${time(segment.start)}`);
        timestamp.addEventListener('click', async () => {
          try {
            if (player.readyState < 1) {
              if (player.error) throw new Error('Recording could not be loaded');
              await new Promise((resolve, reject) => {
                const done = () => { cleanup(); resolve(); };
                const failed = () => { cleanup(); reject(new Error('Recording could not be loaded')); };
                const timeout = setTimeout(failed, 15000);
                const cleanup = () => { clearTimeout(timeout); player.removeEventListener('loadedmetadata', done); player.removeEventListener('error', failed); };
                player.addEventListener('loadedmetadata', done, {once:true});
                player.addEventListener('error', failed, {once:true});
              });
            }
            if (current !== generation) return;
            player.currentTime = segment.start;
            await player.play();
          } catch (error) {
            if (current === generation && error.name !== 'AbortError') {
              status.textContent = `Playback unavailable: ${error.message}`;
            }
          }
        });
        const text = document.createElement('p');
        text.textContent = segment.text;
        if (segment.uncertain) {
          const flag = document.createElement('span');
          flag.className = 'transcript-uncertain';
          flag.textContent = 'Uncertain — check audio';
          text.append(document.createElement('br'), flag);
        }
        row.append(timestamp, text);
        byId('transcriptSegments').append(row);
      }
    } catch (error) {
      if (current === generation) status.textContent = `Could not load transcript: ${error.message}`;
    }
  };
  document.querySelectorAll('.transcript-open').forEach(button => {
    button.addEventListener('click', () => window.openRecordingTranscript(button.dataset.transcriptName));
  });
})();
