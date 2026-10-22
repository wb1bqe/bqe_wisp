/* Run with node tests/aprs_spots.cjs; no browser packages or network needed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
let document;
class Element {
  constructor() { this.children = []; this.dataset = {}; this.events = {}; this.value = ''; this.textContent = ''; this.isConnected = true; this.classList = {add(){}, remove(){}}; }
  append(...nodes) { for (const node of nodes) node.parent = this; this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, handler) { this.events[name] = handler; }
  focus() { document.activeElement = this; }
  closest() { return this.dataset.id ? this : this.parent?.closest(); }
  querySelector() { return this.children.flatMap(node => [node, ...node.children]).find(node => node.events.click); }
}
const nodes = {};
for (const file of ['bqe_wisp_web.py', 'plugins/aprs_spots.py']) {
  for (const match of fs.readFileSync(path.join(root, file), 'utf8').matchAll(/id="(aprsSpots[^"]+)"/g)) nodes[match[1]] = new Element();
}
document = {activeElement: null, getElementById: id => nodes[id], createElement: () => new Element(), addEventListener(){}};
nodes.aprsSpotsModal.dataset.standalone = 'true';
let markers = [], fits = 0, requests = 0, fail = false;
const map = {setView(){return this}, getZoom(){return 2}, fitBounds(){fits++}, invalidateSize(){}};
const group = {addTo(){return this}, clearLayers(){markers=[]}};
const makeMarker = (position,options)=>({position,options, bindTooltip(label){this.label=label;return this}, on(){return this}, addTo(){markers.push(this);return this}});
const L = {map:()=>map, tileLayer:()=>({addTo(){}}), layerGroup:()=>group,
  circleMarker:makeMarker, marker:makeMarker, divIcon:options=>options};
const spot = (id, key, active=true) => ({id,key,name:key,callsign:key,active,latitude:42,longitude:-71,
  decoded_at:'2026-10-02T22:00:00Z',input_source:'ISS',path:[],log_file:'sample.jsonl',comment:'<img src=x>',audio_seconds:5});
let payload = {ok:true,spots:[spot('new','N0CALL'),spot('killed','BALLOON',false),spot('old','N0CALL'),spot('object-old','BALLOON')],warnings:[],scope:'Test history'};
const context = vm.createContext({document,L,Map,Date,Number,JSON,AbortController,
  window: {addEventListener(){}, close(){}},
  setInterval:()=>1,clearInterval(){},requestAnimationFrame:fn=>fn(),
  fetch:async()=>{requests++;if(fail)throw Error('offline');return {ok:true,json:async()=>payload}}});
vm.runInContext(fs.readFileSync(path.join(root,'plugins/aprs_spots.js'),'utf8'),context);
const settle = () => new Promise(resolve => setImmediate(resolve));
(async()=>{
  await settle();
  assert.equal(requests,1,'standalone viewer loads automatically');
  assert.equal(nodes.aprsSpotsRows.children.length,4);
  assert.equal(markers.length,1,'only latest active station/object positions should be mapped');
  assert.equal(fits,1);
  const row = nodes.aprsSpotsRows.children[0];
  row.children[1].children[0].events.click();
  assert(nodes.aprsSpotsDetail.textContent.includes('N0CALL'));
  assert(nodes.aprsSpotsDetail.textContent.includes('<img src=x>'),'received text must remain plain text');
  await nodes.aprsSpotsRefresh.events.click();
  assert.equal(nodes.aprsSpotsRows.children[0],row,'unchanged polling must preserve controls');
  assert.equal(fits,1,'polling must not reset the map view');
  nodes.aprsSpotsFilter.value='n0call';nodes.aprsSpotsFilter.events.input();
  assert.equal(nodes.aprsSpotsRows.children.length,2);
  fail=true;await nodes.aprsSpotsRefresh.events.click();
  assert(nodes.aprsSpotsMessage.textContent.includes('Showing previously loaded packets'));
  fail=false;await nodes.aprsSpotsRefresh.events.click();
  assert(!nodes.aprsSpotsMessage.textContent.includes('Refresh failed'),'successful retry must clear errors even with unchanged spots');
  payload={...payload,spots:[]};await nodes.aprsSpotsRefresh.events.click();
  assert.equal(markers.length,0);
  assert(nodes.aprsSpotsMessage.textContent.includes('No APRS packets decoded yet'));
  payload={...payload,packets:[{...spot('message','N0CALL'),latitude:null,longitude:null,format:'message',comment:'Hello friend'}]};
  nodes.aprsSpotsFilter.value='';
  await nodes.aprsSpotsRefresh.events.click();
  assert.equal(nodes.aprsSpotsRows.children.length,1);
  assert.equal(markers.length,0,'messages must not acquire inferred map positions');
  nodes.aprsSpotsRows.children[0].children[1].children[0].events.click();
  assert(nodes.aprsSpotsDetail.textContent.includes('No position in this packet'));
  payload={...payload,packets:[{...spot('satellite','SAT'),satellite_pass:true,uploaded:false},
    {...spot('preset','GROUND'),satellite_pass:false,uploaded:true},spot('legacy','OLD')]};
  await nodes.aprsSpotsRefresh.events.click();
  assert.equal(markers[0].options.icon.className,'aprs-map-satellite');
  assert(markers[0].label.textContent.includes('Satellite pass'));
  assert.equal(markers[1].options.color,'#1263a0');
  assert.equal(markers[2].options.color,'#1263a0');
  assert(markers[1].label.textContent.includes('Preset / other reception'));
  nodes.aprsSpotsDone.events.click();const before=requests;
  await nodes.aprsSpotsRefresh.events.click();assert.equal(requests,before);
  console.log('APRS spots UI checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
