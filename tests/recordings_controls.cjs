const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag='div') { this.tag = tag; this.children = []; this.events = {}; this.dataset = {}; this.checked = false; this.hidden = false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  addEventListener(type, fn) { this.events[type] = fn; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelectorAll(selector) {
    const nodes = this.children.flatMap(node => [node, ...(node.querySelectorAll?.('input') || [])]);
    return nodes.filter(node => node.tag === 'input' && (selector !== 'input:checked' || node.checked));
  }
}
const elements = {};
const get = id => elements[id] ||= new Element();
const rows = ['iss', 'ao-91', 'iss'].map((source, i) => {
  const row = new Element('tr'); row.dataset.source = source;
  const box = new Element('input'); box.dataset.name = `${source}-${i}.wav`;
  const cell = new Element('td');
  row.querySelector = query => query === '.morse-checkbox' ? box : cell;
  return row;
});
get('satelliteFilterDialog').append(get('satelliteFilterChoices'));
const requests = [];
let server = {running: false, decoder: null, items: []};
const context = {
  document: {getElementById: get, querySelectorAll: () => rows,
    createElement: tag => new Element(tag), createTextNode: text => ({textContent: text})},
  window: {}, morseDecodeRunning: false,
  sessionStorage: {getItem: () => null, setItem() {}},
  setTimeout() {}, clearTimeout() {},
  updateMorseSelectionState() { context.window.updateRecordingDecodeSelection(); },
  fetch: async (url, options) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); requests.push(body);
      server = {running: true, decoder: body.decoder, items: body.names.map(name => ({name, state: 'queued'}))};
    }
    return {ok: true, json: async () => server};
  }
};
vm.runInNewContext(fs.readFileSync('plugins/recordings_controls.js', 'utf8'), context);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await tick();
  assert.equal(get('sstvDecodeButton').disabled, true);
  assert.equal(get('speechDecodeButton').disabled, true);
  get('satelliteFilterButton').events.click();
  assert.equal(get('satelliteFilterDialog').open, true);
  get('filterSelectNone').events.click();
  const boxes = get('satelliteFilterChoices').querySelectorAll('input');
  boxes.find(box => box.value === 'iss').checked = true;
  rows.forEach(row => { row.querySelector('.morse-checkbox').checked = true; });
  get('filterApply').events.click();
  assert.deepEqual(rows.map(row => row.hidden), [false, true, false]);
  assert.equal(rows[1].querySelector('.morse-checkbox').checked, false);
  assert.equal(get('recordingFilterStatus').textContent, '2 of 3 recordings shown');
  assert.equal(get('sstvDecodeButton').disabled, false);
  assert.equal(get('speechDecodeButton').disabled, false);
  get('satelliteFilterButton').events.click();
  get('filterSelectNone').events.click();
  get('filterCancel').events.click();
  assert.equal(rows[0].hidden, false, 'Cancel must preserve applied filter');
  get('aprsDecodeButton').events.click();
  await tick();
  assert.equal(requests[0].decoder, 'aprs');
  assert.deepEqual(requests[0].names, ['iss-0.wav', 'iss-2.wav']);
  assert.equal(get('sstvDecodeButton').disabled, true, 'Batch disables duplicate starts');
  assert.equal(get('speechDecodeButton').disabled, true);
  assert.equal(get('cancelRecordingDecode').hidden, false);
  get('satelliteFilterButton').events.click();
  get('filterSelectNone').events.click();
  get('filterApply').events.click();
  assert.equal(get('noFilteredRecordings').hidden, false);
  assert.equal(get('recordingFilterStatus').textContent, '0 of 3 recordings shown');
  console.log('Recordings filter, selection, cancel, empty state and decode submission checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
