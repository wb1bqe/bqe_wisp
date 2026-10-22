const assert = require('node:assert/strict');
const {series, geometry} = require('../plugins/telemetry_charts.js');
const packet = (id, seconds, value, extras = {}) => ({id, packet: {
  source: 'RS57S', decoded_at: new Date(1700000000000 + seconds * 1000).toISOString(),
  telemetry: [{message_id: 'beacon', sender: 2, fields: {temperature: value}}], ...extras
}});
const first = packet('a', 0, -10), mid = packet('b', 10, 0), last = packet('c', 100, 10);
const read = items => series(items, first, first.packet.telemetry[0], 'temperature');
const trend = geometry(read([last, first, mid]));
assert.deepEqual(trend.dots.map(p => p.value), [-10, 0, 10]);
assert.equal(trend.min, -10); assert.equal(trend.max, 10);
assert.ok(Math.abs((trend.dots[1].x - trend.dots[0].x) / (trend.dots[2].x - trend.dots[0].x) - .1) < 1e-10,
  'The x axis must use elapsed time, not sample index');
for (const missing of [null, undefined, '0', false, NaN, Infinity]) {
  const g = geometry(read([first, packet('gap', 50, missing), last]));
  assert.equal(g.count, 2); assert.equal(g.path.includes('L'), false, 'Unavailable samples must break the line');
}
assert.equal(geometry(read([packet('x', 0, null)])), null);
for (const items of [[first], [first, packet('same', 10, -10)]]) {
  const g = geometry(read(items));
  assert.ok(g.dots.every(p => Number.isFinite(p.x) && p.y === 26), 'Single and constant readings must be visible');
}
assert.equal(geometry(read([first, packet('same-time', 0, 10)])).dots[1].x, 120);
assert.equal(read([first, packet('source', 10, 100, {source:'OTHER'}),
  packet('type', 10, 100, {telemetry:[{message_id:'other',sender:2,fields:{temperature:100}}]}),
  packet('sender', 10, 100, {telemetry:[{message_id:'beacon',sender:3,fields:{temperature:100}}]}),
  packet('bad-time', 10, 100, {decoded_at:'unknown'})]).length, 1);
console.log('Telemetry chart checks passed.');
