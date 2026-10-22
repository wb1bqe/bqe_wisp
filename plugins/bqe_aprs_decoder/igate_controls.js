/* Credentials are write-only; no browser password-store access. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id),form=$('igateSettings');
 if(!form)return;
 const endpoint=form.dataset.endpoint;
 let initialized=false;
 async function request(options){
  const response=await fetch(endpoint,options);
  const value=await response.json();
  if(!response.ok)throw Error(value.error||'Could not update APRS-IS settings');
  return value;
 }
 function show(data){
  $('igateCall').textContent=data.callsign||'Set your callsign in QTH configuration';
  if(!initialized){
   $('igateEnabled').checked=data.enabled;$('igateHost').value=data.host;$('igatePort').value=data.port;
   initialized=true;
  }
  $('igatePasscode').placeholder=data.has_passcode?'Saved — leave blank to keep':'Enter APRS-IS passcode';
  const state=data.upload||{};
  $('igateConnection').textContent=`${state.state||'inactive'}: ${state.message||''} · Sent ${state.sent||0} · Filtered ${state.filtered||0} · Dropped ${state.dropped||0}`;
  $('igateConnection').textContent+=` · Station beacons ${state.beacons_sent||0}${state.last_beacon_at?' · Last beacon '+new Date(state.last_beacon_at).toLocaleString():''}${state.beacon_message?' · '+state.beacon_message:''}`;
 }
 async function refresh(){try{show(await request({cache:'no-store'}));}catch(error){$('igateConnection').textContent=error.message;}}
 $('igateSave').addEventListener('click',async()=>{
  $('igateSave').disabled=true;
  try{
   const data=await request({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:$('igateEnabled').checked,host:$('igateHost').value.trim(),port:Number($('igatePort').value),passcode:$('igatePasscode').value.trim(),clear_passcode:$('igateClear').checked})});
   $('igatePasscode').value='';$('igateClear').checked=false;show(data);
   $('igateSaveStatus').textContent=data.enabled?'Saved. Uploading applies to live radio reception only.':'Saved. Uploading is disabled.';
  }catch(error){$('igateSaveStatus').textContent=error.message;}
  finally{$('igateSave').disabled=false;}
 });
 refresh();setInterval(refresh,3000);
})();
