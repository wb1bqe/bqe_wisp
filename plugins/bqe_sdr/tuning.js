/* One wheel event or arrow key changes the downlink by exactly 100 Hz. */
let knobTarget = null, knobBusy = false, knobAngle = 0;
const knob = document.getElementById('frequencyKnob');
function updateKnob() {
  knob.setAttribute('aria-disabled', String(current.state !== 'running'));
  knob.setAttribute('aria-valuenow', String(knobTarget ?? current.frequency ?? 0));
  knob.setAttribute('aria-valuetext', ((knobTarget ?? current.frequency ?? 0) / 1e6).toFixed(6) + ' MHz');
}
async function turnKnob(direction) {
  if (current.state !== 'running') return;
  knobTarget = (knobTarget ?? Math.round(current.frequency)) + direction * 100;
  knobAngle += direction * 8;
  knob.style.setProperty('--angle', knobAngle + 'deg');
  $('frequency').value = (knobTarget / 1e6).toFixed(6);
  updateKnob();
  if (knobBusy) return;
  knobBusy = true;
  $('tune').disabled = true;
  try {
    while (knobTarget !== null) {
      const requested = knobTarget;
      const response = await fetch('/tune', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({frequency: requested})});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || 'Tuning failed');
      current.frequency = requested;
      $('tuned').textContent = (requested / 1e6).toFixed(6);
      if (knobTarget === requested) knobTarget = null;
    }
    $('message').textContent = '';
  } catch (error) {
    knobTarget = null;
    $('message').textContent = error.message;
  } finally {
    knobBusy = false;
    await refresh();
    $('frequency').value = (current.frequency / 1e6).toFixed(6);
    updateKnob();
  }
}
knob.addEventListener('wheel', event => {
  if (!event.deltaY || event.ctrlKey) return;
  event.preventDefault();
  turnKnob(event.deltaY < 0 ? 1 : -1);
}, {passive: false});
knob.addEventListener('keydown', event => {
  if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  turnKnob(['ArrowUp', 'ArrowRight'].includes(event.key) ? 1 : -1);
});
updateKnob();
