/* Run with node tests/decoder_lifecycle.cjs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../plugins/decoder_lifecycle.js'), 'utf8');
function setup() {
  let now = 0, next = 0, closes = 0, destination = null;
  const timers = new Map();
  const window = {
    fetch: async () => { throw new TypeError('NetworkError when attempting to fetch resource.'); },
    setTimeout(fn, delay) { const id = ++next; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    close() { closes++; },
    location: {replace(url) { destination = url; }},
  };
  vm.runInNewContext(source, {window, AbortController, Date: {now: () => now}});
  return {window, timers, page: window.BqePluginPage,
    time(value) { now = value; },
    run(delay) {
      const entry = [...timers].find(([, t]) => t.delay === delay);
      assert.ok(entry, `Expected timer with delay ${delay}`);
      timers.delete(entry[0]); return entry[1].fn();
    },
    get closes() { return closes; }, get destination() { return destination; }};
}
async function fail(s, time) {
  s.time(time);
  await assert.rejects(s.page.fetch('/status'));
}
(async () => {
  const s = setup();
  await fail(s, 0); await fail(s, 1000); await fail(s, 2000);
  assert.equal(s.timers.size, 0, 'brief interruptions must not unload');
  await fail(s, 5000);
  s.run(0); assert.equal(s.closes, 1);
  s.run(100); assert.equal(s.destination, 'about:blank');
  let polls = 0;
  s.page.poll(async () => { polls++; }, 250);
  assert.equal(polls, 0, 'polling stops after sustained disconnect');

  const recovered = setup();
  await fail(recovered, 0); await fail(recovered, 3000);
  const networkFailure = recovered.window.fetch;
  recovered.window.fetch = async () => ({ok: false, status: 500});
  assert.equal((await recovered.page.fetch('/status')).status, 500);
  recovered.window.fetch = networkFailure;
  await fail(recovered, 6000); await fail(recovered, 7000);
  assert.equal(recovered.timers.size, 0, 'any HTTP response resets disconnect count');
  await assert.rejects(recovered.page.fetch('/file'));
  assert.equal(recovered.timers.size, 0, 'action failures do not count as status failures');

  const hung = setup();
  hung.window.fetch = (_, init) => new Promise((resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new Error('aborted')));
  });
  const pending = assert.rejects(hung.page.fetch('/status'), /aborted/);
  hung.run(5000); await pending;
  assert.equal(hung.timers.size, 0, 'timed-out requests clean up their timer');

  const sequential = setup();
  let finish;
  sequential.page.poll(() => new Promise(resolve => { finish = resolve; }), 250);
  assert.equal(sequential.timers.size, 0, 'no overlapping polls');
  finish(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(sequential.timers.size, 1);
  console.log('Decoder lifecycle tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
