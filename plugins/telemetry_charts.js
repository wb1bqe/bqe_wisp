/* Pure time-series helpers shared by the viewer and its offline tests. */
((root) => {
  'use strict';
  function key(packet, record) {
    return JSON.stringify([packet.source || '', record.record_type || record.message_id || record.name || '',
      record.sender ?? null, record.receiver ?? null]);
  }
  function series(packets, selected, record, field) {
    const identity = key(selected.packet, record), points = [];
    for (const item of packets) {
      const stamp = item.packet.decoded_at;
      const timestamp = typeof stamp === 'string' ? Date.parse(stamp) : NaN;
      if (!Number.isFinite(timestamp)) continue;
      for (const entry of Array.isArray(item.packet.telemetry) ? item.packet.telemetry : []) {
        if (!entry || key(item.packet, entry) !== identity) continue;
        const value = entry.fields?.[field];
        // Keep a gap for unavailable values; never turn null/string/bool into 0.
        points.push({timestamp, value: typeof value === 'number' && Number.isFinite(value) ? value : null,
          id: item.id});
      }
    }
    return points.sort((a, b) => a.timestamp - b.timestamp || a.id.localeCompare(b.id));
  }
  function geometry(points) {
    const valid = points.filter(p => p.value !== null);
    if (!valid.length) return null;
    const min = Math.min(...valid.map(p => p.value)), max = Math.max(...valid.map(p => p.value));
    const start = points[0].timestamp, end = points[points.length - 1].timestamp;
    const span = max - min;
    const dots = points.map(p => ({...p,
      x: end === start ? 120 : 6 + (p.timestamp - start) / (end - start) * 228,
      y: p.value === null ? null : span === 0 ? 26 : 46 - (p.value - min) / span * 40}));
    let connected = false;
    const path = dots.map(p => {
      if (p.y === null) { connected = false; return ''; }
      const command = connected ? 'L' : 'M'; connected = true;
      return `${command}${p.x.toFixed(2)},${p.y.toFixed(2)}`;
    }).filter(Boolean).join(' ');
    return {min, max, start, end, dots: dots.filter(p => p.y !== null), path, count: valid.length};
  }
  const api = {series, geometry};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.BQETelemetryCharts = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
