'use strict';
(() => {
 const $ = id => document.getElementById(id);
 let token='', devices=[], timer, activeView='devices';
 const status=text=>{$('status').textContent=text;};
 const element=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
 const percent=(metrics,kind)=>{const total=metrics?.[kind+'_total_bytes'];return total>0?100*metrics[kind+'_used_bytes']/total:null;};
 const number=value=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(1)+'%':'—';
 const bytes=value=>typeof value==='number'?(value/1073741824).toFixed(1)+' GiB':'—';
 const when=value=>value?new Date(value*1000).toLocaleString():'Not connected yet';
 function logout(message='Signed out') {
  token='';devices=[];clearInterval(timer);$('app').hidden=true;$('login-screen').hidden=false;$('devices').replaceChildren();$('audit').replaceChildren();$('details').replaceChildren();$('device-dialog').close();$('token').value='';status(message);
 }
 async function api(path,options={}) {
  const response=await fetch(path,{...options,headers:{Authorization:'Bearer '+token,...options.headers}});
  if(response.status===401){logout('Authentication failed. Sign in with a valid admin token.');throw Error('Authentication failed');}
  if(!response.ok){let body={};try{body=await response.json();}catch{}throw Error(body.error||'Request failed ('+response.status+')');}
  return response;
 }
 function metric(value) {
  const cell=element('td',undefined,'metric');cell.append(element('span',number(value)));
  if(typeof value==='number'){const bar=element('div',undefined,'bar');const fill=element('span');fill.style.width=Math.max(0,Math.min(100,value))+'%';bar.append(fill);cell.append(bar);}return cell;
 }
 function render() {
  for(const key of ['online','offline','pending'])$(key).textContent=devices.filter(d=>d.status===key).length;
  $('total').textContent=devices.length;
  const query=$('search').value.toLowerCase(),filter=$('filter').value;
  const shown=devices.filter(d=>(filter==='all'||d.status===filter)&&(d.inventory.hostname+' '+d.id).toLowerCase().includes(query));
  $('devices').replaceChildren();$('empty').hidden=shown.length>0;
  $('empty').textContent=devices.length?'No devices match your search.':'No devices yet. Download an agent installer to connect your first computer.';
  for(const device of shown){
   const inventory=device.inventory,metrics=inventory.metrics||{},row=element('tr');
   const name=element('td');name.append(element('span',inventory.hostname,'device-name'),element('small',inventory.os+' '+inventory.release+' · '+inventory.architecture,'sub'));
   const state=element('td');state.append(element('span',device.status==='pending'?'Awaiting installation':device.status[0].toUpperCase()+device.status.slice(1),'badge '+device.status));
   row.append(name,state,metric(metrics.cpu_percent),metric(percent(metrics,'memory')),metric(percent(metrics,'disk')),element('td',when(device.last_seen)));
   const actions=element('td'),wrap=element('div',undefined,'row-actions'),detail=element('button','Details');detail.addEventListener('click',()=>details(device.id));wrap.append(detail);
   if(!device.revoked){const revoke=element('button','Revoke');revoke.addEventListener('click',()=>revokeDevice(device.id));wrap.append(revoke);}
   actions.append(wrap);row.append(actions);$('devices').append(row);
  }
 }
 async function loadDevices(){const response=await api('/api/devices');devices=(await response.json()).devices;render();$('updated').textContent='Last refreshed '+new Date().toLocaleTimeString()+' · Refreshes automatically every 30 seconds';}
 async function showView(view){activeView=view;for(const name of ['devices','agents','audit'])$('view-'+name).hidden=name!==view;document.querySelectorAll('[data-view]').forEach(button=>{if(button.dataset.view===view)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');});$('page-title').textContent={devices:'Fleet overview',agents:'Agent installers',audit:'Activity log'}[view];try{if(view==='agents'){const info=await(await api('/api/agents')).json();$('build-status').textContent=info.available?'Native agent v'+info.version+' · '+info.platform+' · Unsigned':info.reason;$('download-agent').disabled=!info.available;}if(view==='audit'){const data=await(await api('/api/audit')).json();$('audit').replaceChildren();for(const event of data.events){const row=element('tr');for(const value of [event.event.replaceAll('_',' '),event.device_id||'—',when(event.occurred_at)])row.append(element('td',value));$('audit').append(row);}}}catch(error){status(error.message);}}
 async function download(){
  $('download-top').disabled=true;$('download-agent').disabled=true;
  try{const response=await api('/api/agents/windows/download',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const blob=await response.blob();const url=URL.createObjectURL(blob),link=element('a');link.href=url;link.download=(response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)||[])[1]||'hybridrmm-agent.zip';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);await loadDevices();status('Installer downloaded. Extract it on the target Windows computer and run install.ps1 as Administrator. Keep this device-specific ZIP private.');}
  catch(error){status('Download failed: '+error.message);}finally{$('download-top').disabled=false;$('download-agent').disabled=false;}
 }
 async function revokeDevice(id){if(!window.confirm('Revoke this device credential? Future reports and new screen sessions will be rejected. This does not uninstall the local service.'))return;try{await api('/api/devices/'+id+'/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await loadDevices();status('Device credential revoked. Uninstall the service locally if it is no longer needed.');}catch(error){status(error.message);}}
 async function details(id){try{const data=await(await api('/api/devices/'+id)).json(),device=data.device,inv=device.inventory,m=inv.metrics||{};$('detail-title').textContent=inv.hostname;$('details').replaceChildren();const fields={'Device ID':id,'Status':device.status,'Operating system':inv.os+' '+inv.release,'Architecture':inv.architecture,'Agent version':inv.agent_version,'Last report':when(device.last_seen),'CPU':number(m.cpu_percent),'Memory':bytes(m.memory_used_bytes)+' / '+bytes(m.memory_total_bytes),'System disk':bytes(m.disk_used_bytes)+' / '+bytes(m.disk_total_bytes),'Uptime':typeof m.uptime_seconds==='number'?Math.floor(m.uptime_seconds/3600)+' hours':'—'};for(const[key,value]of Object.entries(fields))$('details').append(element('dt',key),element('dd',value));$('history').replaceChildren();const readings=data.history.filter(s=>typeof s.metrics.cpu_percent==='number').reverse();if(readings.length<2)$('history').append(element('p','More readings will appear as this agent reports.','muted'));else{const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'),line=document.createElementNS(ns,'polyline');svg.setAttribute('viewBox','0 0 500 100');svg.setAttribute('role','img');svg.setAttribute('aria-label','Recent CPU percentage, oldest to newest');line.setAttribute('points',readings.map((s,i)=>(i*500/(readings.length-1))+','+(100-s.metrics.cpu_percent)).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke','#66e6b2');line.setAttribute('stroke-width','2');svg.append(line);$('history').append(svg);}$('detail-viewer').href='/viewer#'+encodeURIComponent(id);$('detail-viewer').hidden=device.revoked||device.status==='pending';$('device-dialog').showModal();}catch(error){status(error.message);}}
 $('login').addEventListener('submit',async event=>{event.preventDefault();token=$('token').value;try{await loadDevices();$('token').value='';$('login-screen').hidden=true;$('app').hidden=false;await showView('devices');status('Dashboard connected. Download an agent to start monitoring a computer.');clearInterval(timer);timer=setInterval(()=>loadDevices().catch(error=>status('Refresh failed: '+error.message)),30000);}catch(error){status(error.message);}});
 $('logout').addEventListener('click',()=>logout());$('logout-mobile').addEventListener('click',()=>logout());$('refresh').addEventListener('click',()=>loadDevices().then(()=>status('Device inventory refreshed.')).catch(error=>status(error.message)));
 for(const id of ['download-top','download-agent'])$(id).addEventListener('click',download);
 document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>showView(button.dataset.view)));
 $('search').addEventListener('input',render);$('filter').addEventListener('change',render);$('close-details').addEventListener('click',()=>$('device-dialog').close());
})();
