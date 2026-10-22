// Live, session-only controls. Serialize requests and coalesce rapid drags.
let gainBusy=false, gainPending={}, gainDragging=false;
function updateGains(){
 const running=current.state==='running', tuner=running&&current.live_tuner_gain;
 $('tunerSlider').disabled=!tuner;$('tunerAuto').disabled=!tuner;
 $('audioSlider').disabled=!running;
 if(gainBusy||gainDragging||Object.keys(gainPending).length)return;
 const s=current.settings;if(!s)return;
 const gains=current.tuner_gains||[];
 const slider=$('tunerSlider');
 slider.min=gains.length?0:0;slider.max=gains.length?gains.length-1:49.6;slider.step=gains.length?1:.1;
 $('tunerAuto').checked=s.gain==null;
 if(s.gain!=null){
  slider.value=gains.length?gains.reduce((best,g,i)=>Math.abs(g-s.gain)<Math.abs(gains[best]-s.gain)?i:best,0):s.gain;
 }
 $('tunerValue').textContent=s.gain==null?'Automatic':Number(s.gain).toFixed(1)+' dB';
 $('audioSlider').max=Math.max(5,Number(s.audio_gain));
 $('audioSlider').value=s.audio_gain;
 $('audioValue').textContent=Number(s.audio_gain).toFixed(2)+'×';
}
async function sendGains(values){
 Object.assign(gainPending,values);
 if(gainBusy)return;
 gainBusy=true;
 try{
  while(Object.keys(gainPending).length){
   const change=gainPending;gainPending={};
   const response=await fetch('/gains',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(change)});
   const result=await response.json();if(!response.ok)throw Error(result.error||'Gain change failed');
   current.settings={...current.settings,gain:result.gain,audio_gain:result.audio_gain};
   if('gain' in change)$('gain').value=result.gain==null?'':result.gain;
   if('audio_gain' in change)$('audio_gain').value=result.audio_gain;
  }
  $('gainMessage').textContent='Applied to this receiver session.';
 }catch(error){gainPending={};$('gainMessage').textContent=error.message;}
 finally{gainBusy=false;await refresh();updateGains();}
}
for(const id of ['tunerSlider','audioSlider']){
 $(id).addEventListener('pointerdown',()=>gainDragging=true);
 $(id).addEventListener('change',()=>{gainDragging=false;updateGains();});
}
window.addEventListener('pointerup',()=>{gainDragging=false;updateGains();});
window.addEventListener('pointercancel',()=>{gainDragging=false;updateGains();});
$('tunerSlider').addEventListener('input',()=>{
 const gains=current.tuner_gains||[],value=gains.length?gains[Number($('tunerSlider').value)]:Number($('tunerSlider').value);
 $('tunerAuto').checked=false;$('tunerValue').textContent=value.toFixed(1)+' dB';sendGains({gain:value});
});
$('tunerAuto').addEventListener('change',()=>{
 const gains=current.tuner_gains||[];
 const value=$('tunerAuto').checked?null:gains.length?gains[Number($('tunerSlider').value)]:Number($('tunerSlider').value);
 $('tunerValue').textContent=value==null?'Automatic':value.toFixed(1)+' dB';sendGains({gain:value});
});
$('audioSlider').addEventListener('input',()=>{
 const value=Number($('audioSlider').value);$('audioValue').textContent=value.toFixed(2)+'×';sendGains({audio_gain:value});
});
updateGains();
