"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let status, order=[], catalog=[], defaults=true, dirty=false, pending=false, dragging=false, textDirty=false;
  const selectedStrategy=()=>document.querySelector('input[name=strategy]:checked').value;
  const descending=items=>[...items].sort((a,b)=>{const x=a.split('-').map(Number),y=b.split('-').map(Number);return y[0]-x[0]||y[1]-x[1];});
  const message=text=>{$('ap-message').textContent=text;$('ap-message').hidden=false;};
  const error=text=>{$('ap-error').textContent=text;$('ap-error').hidden=false;};
  const busy=()=>pending||!status||status.demo;
  function controls(){
    const locked=busy()||status?.current_job||status?.queue?.length;
    $('ap-fields').disabled=Boolean(locked);
    $('ap-save').disabled=Boolean(locked||!dirty);
    $('ap-run').disabled=Boolean(busy()||dirty);
    $('ap-scan').disabled=Boolean(busy());
    $('ap-dirty').textContent=dirty?'not saved yet':locked?'settings wait until the queue is clear.':'settings saved.';
    $('ap-rotation-section').hidden=selectedStrategy()!=='elephs';
  }
  function changed(){dirty=true;controls();}
  function edit(next){textDirty=false;order=next;defaults=false;changed();renderLists();}
  function add(stage,index=order.length){
    if(!catalog.includes(stage))return;
    const next=order.filter(s=>s!==stage);next.splice(Math.min(index,next.length),0,stage);edit(next);
  }
  function button(label,aria,action){const b=document.createElement('button');b.type='button';b.className='button secondary small';b.textContent=label;b.setAttribute('aria-label',aria);b.addEventListener('click',action);return b;}
  function renderLists(){
    if(dragging)return;
    const query=$('ap-search').value.trim();
    const potential=catalog.filter(s=>!order.includes(s)&&s.includes(query));
    $('ap-potential-count').textContent=`(${potential.length})`;$('ap-order-count').textContent=`(${order.length})`;
    for(const [id,stages,selected] of [['ap-potential',potential,false],['ap-order',order,true]]){
      const fragment=document.createDocumentFragment();
      stages.forEach((stage,index)=>{
        const row=document.createElement('li');row.draggable=true;row.dataset.stage=stage;
        const title=document.createElement('strong');title.textContent=stage;
        const stars=document.createElement('small');stars.textContent='★★★';stars.setAttribute('aria-label','three stars');row.append(title,stars);
        if(selected){
          const up=button('↑',`Move ${stage} up`,()=>{const next=[...order];[next[index-1],next[index]]=[next[index],next[index-1]];edit(next);});up.disabled=index===0;
          const down=button('↓',`Move ${stage} down`,()=>{const next=[...order];[next[index+1],next[index]]=[next[index],next[index+1]];edit(next);});down.disabled=index===order.length-1;
          row.append(up,down,button('×',`Remove ${stage}`,()=>edit(order.filter(s=>s!==stage))));
        }else row.append(button('+',`Add ${stage}`,()=>add(stage)));
        row.addEventListener('dragstart',e=>{if($('ap-fields').disabled){e.preventDefault();return;}dragging=true;e.dataTransfer.setData('text/plain',stage);e.dataTransfer.effectAllowed='move';});
        row.addEventListener('dragend',()=>{dragging=false;renderLists();});
        if(selected){
          row.addEventListener('dragover',e=>{e.preventDefault();row.classList.add('drop-before');});
          row.addEventListener('dragleave',()=>row.classList.remove('drop-before'));
          row.addEventListener('drop',e=>{e.preventDefault();e.stopPropagation();if($('ap-fields').disabled)return;const s=e.dataTransfer.getData('text/plain');dragging=false;if(s===stage)return;const remaining=order.filter(x=>x!==s);add(s,Math.max(0,remaining.indexOf(stage)));});
        }
        fragment.append(row);
      });
      if(!stages.length){const row=document.createElement('li');row.className='empty';row.textContent=selected?'drop stages here, or use the + buttons.':catalog.length?'all matching stages are in your rotation.':'scan stages to fill this list.';fragment.append(row);}
      $(id).replaceChildren(fragment);
    }
    if(!textDirty)$('ap-order-text').value=order.join('\n');$('ap-empty-warning').hidden=order.length!==0;
  }
  async function post(path,body){
    pending=true;controls();$('ap-error').hidden=true;
    try{
      const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':status.csrf_token},body:JSON.stringify(body)});
      const result=await response.json();if(!response.ok)throw new Error(result.error||'request failed');return result;
    }catch(e){error(e.message);return null;}finally{pending=false;controls();}
  }
  async function refresh(){
    try{
      const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw new Error('dashboard unavailable');
      status=await response.json();const ap=status.schedule?.ap||{};const c=status.config||{};
      const previous=catalog.join(',');catalog=descending(ap.hard_stages||[]);
      if(!dirty){
        $('ap-floor').value=c.ap_floor??100;$('ap-enabled').checked=c.ap_schedule_enabled===true;
        const value=['elephs','reports','credits'].includes(c.ap_strategy)?c.ap_strategy:'elephs';document.querySelector(`input[name=strategy][value=${value}]`).checked=true;
        defaults=c.ap_hard_default_order!==false;const next=defaults?[...catalog]:[...(c.ap_hard_order||[])];
        if(order.join(',')!==next.join(',')||previous!==catalog.join(',')){order=next;renderLists();}
      }else if(previous!==catalog.join(','))renderLists();
      $('ap-balance').textContent=ap.last_ap==null?'— AP':`${ap.last_ap} AP`;
      $('ap-summary').textContent=ap.blocked_reason?`paused: ${ap.blocked_reason}`:ap.pending?'an earlier sweep needs its result checked.':ap.last_summary||'scan your stages, pick a plan, and save it.';
      $('ap-schedule').textContent=ap.blocked_reason?'automatic spending is paused. check the log, then explicitly queue Spend AP.':ap.enabled?`enabled · ${status.queue_paused?'waiting for the queue to resume':ap.next_check_at?'next check '+new Date(ap.next_check_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'checks when the queue is ready'}`:'automatic spending is off. queue a visit whenever you want.';
      $('ap-survey').textContent=ap.surveyed_at?`${catalog.length} three-star Hard stages · scanned ${new Date(ap.surveyed_at).toLocaleString()}. rescan after clearing more.`:'scan stages first. the scan doesn’t spend AP.';
      $('ap-cursor').textContent=ap.next_stage?`next up: ${ap.next_stage}. used-up stages are skipped until reset.`:'one sweep each. skip used-up stages until reset.';
      for(const kind of ['reports','credits'])$(`${kind}-best`).textContent=ap.commissions?.[kind]?`best verified: ${ap.commissions[kind]}`:'scan to find your best stage';
      controls();
    }catch(e){status=null;controls();error(e.message);}
  }
  $('ap-form').addEventListener('input',e=>{if(e.target.id==='ap-order-text')textDirty=true;if(e.target.id!=='ap-search')changed();});
  $('ap-search').addEventListener('input',renderLists);
  $('ap-default').addEventListener('click',()=>{textDirty=false;order=[...catalog];defaults=true;changed();renderLists();});
  $('ap-apply-text').addEventListener('click',()=>{
    const stages=$('ap-order-text').value.split(/[\s,]+/).filter(Boolean);
    if(new Set(stages).size!==stages.length||stages.some(s=>!catalog.includes(s))){error('use each scanned three-star stage at most once.');return;}edit(stages);$('ap-error').hidden=true;
  });
  for(const [id,selected] of [['ap-order',true],['ap-potential',false]]){
    $(id).addEventListener('dragover',e=>{if(!$('ap-fields').disabled)e.preventDefault();});
    $(id).addEventListener('drop',e=>{e.preventDefault();if($('ap-fields').disabled)return;dragging=false;const s=e.dataTransfer.getData('text/plain');if(selected)add(s);else edit(order.filter(x=>x!==s));});
  }
  $('ap-form').addEventListener('submit',async e=>{
    e.preventDefault();if(!$('ap-form').reportValidity())return;
    const typed=$('ap-order-text').value.split(/[\s,]+/).filter(Boolean);
    if(typed.join(',')!==order.join(',')){
      if(new Set(typed).size!==typed.length||typed.some(s=>!catalog.includes(s))){error('use each scanned three-star stage at most once.');return;}
      order=typed;defaults=false;
    }
    const result=await post('/api/settings',{ap_floor:Number($('ap-floor').value),ap_schedule_enabled:$('ap-enabled').checked,ap_strategy:selectedStrategy(),ap_hard_default_order:defaults,ap_hard_order:defaults?[]:order});
    if(result!==null){dirty=false;textDirty=false;message('AP settings saved.');await refresh();}
  });
  $('ap-scan').addEventListener('click',async()=>{if(await post('/api/run',{task:'scan_ap'})!==null){message('stage scan queued. no AP will be spent.');await refresh();}});
  $('ap-run').addEventListener('click',async()=>{if(await post('/api/run',{task:'spend_ap'})!==null){message('spend AP is in the queue.');await refresh();}});
  renderLists();void refresh();setInterval(()=>{if(!document.hidden&&!pending)void refresh();},2000);
})();
