const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('bqe_wisp_web.py', 'utf8');
class Element {
  constructor() { this.dataset = {}; this.children = []; this.classes = new Set(); this.classList = {toggle:(key,on)=>on ? this.classes.add(key) : this.classes.delete(key)}; }
  setAttribute(key,value) { this[key]=value; }
  querySelector() { return null; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
}
const nodes = Object.fromEntries(['satelliteStatusIndicator','satelliteStatusText','pluginStatusIndicators'].map(id=>[id,new Element()]));
const context = vm.createContext({document:{getElementById:id=>nodes[id],createElement:()=>new Element()},
  text:value=>value==null?'':String(value),set:(id,value)=>{nodes[id].textContent=value;}});
vm.runInContext(source.slice(source.indexOf('    function updateStateIndicator('),source.indexOf('    function clearMenuButtonRestoreStyle(')),context);
function update(data) { context.data=data;vm.runInContext('updateRuntimeIndicators(data)',context); }
update({tracking_running:true,current_satellite:'LUCA-1',running_plugins:[{id:'sstv',label:'SSTV'},{id:'tlm',label:'Telemetry'}]});
assert(nodes.satelliteStatusIndicator.classes.has('enabled'));
assert.equal(nodes.satelliteStatusText.textContent,'LUCA-1');
assert.equal(nodes.pluginStatusIndicators.children.length,2);
const first=nodes.pluginStatusIndicators.children[0];
vm.runInContext('updateRuntimeIndicators(data)',context);
assert.equal(nodes.pluginStatusIndicators.children[0],first,'polling must preserve unchanged indicators');
update({tracking_running:false,tracking_status:{satellite:'stale'},running_plugins:[{id:'aprs',label:'<img src=x>'}]});
assert.equal(nodes.satelliteStatusText.textContent,'Idle');
assert(nodes.satelliteStatusIndicator.classes.has('disabled'));
assert.equal(nodes.pluginStatusIndicators.children[0].children[1].textContent,'<img src=x>');
update({tracking_running:false,running_plugins:[]});
assert.equal(nodes.pluginStatusIndicators.children.length,0);
update({running_plugins:[{id:'pass_audio',label:'Audio recorder'}],external_audio_recording:{status:'recording'}});
assert.equal(nodes.pluginStatusIndicators.children.length,1,'recording status must not duplicate pass recorder');
console.log('Runtime indicator checks passed');
