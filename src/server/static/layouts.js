/* Canvas controls overlay native C-rendered pixels; no second text renderer. */
(() => {
  'use strict';
  const root=document.getElementById('layout-editor'); if(!root) return;
  const data=JSON.parse(document.getElementById('layout-data').textContent), s=data.strings;
  const $=q=>root.querySelector(q), clone=o=>JSON.parse(JSON.stringify(o));
  const templates=data.templates, drafts=new Map(), picker=$('[data-layout-select]'), elements=$('[data-element-select]');
  const canvas=$('[data-canvas]'), regions=$('[data-regions]'), form=$('[data-properties]'), name=$('[data-name]');
  let record, layout, selected=null, undo=[], redo=[], dirty=false, issues=[], timer, controller, generation=0, objectUrl, pending=false, busy=false;
  const message=(text,error=false)=>{ const p=$('[data-message]'); p.textContent=text; p.classList.toggle('error',error); };
  const current=()=>layout.elements.find(e=>e.id===selected);
  const locked=()=>record.builtin;
  const snapshot=()=>JSON.stringify({layout,name:name.value});
  const changed=()=>{ dirty=snapshot()!==JSON.stringify({layout:record.layout,name:record.name}); drafts.set(record.id,{layout:clone(layout),name:name.value}); controls(); };
  const remember=()=>{ undo.push(snapshot()); if(undo.length>30) undo.shift(); redo=[]; };
  function controls() {
    root.querySelector('[data-action="save"]').disabled=locked()||busy||!dirty;
    root.querySelector('[data-action="delete"]').disabled=locked()||busy||record.id===data.active.id;
    root.querySelector('[data-action="apply"]').disabled=busy||pending||issues.length>0;
    root.querySelector('[data-action="undo"]').disabled=locked()||!undo.length;
    root.querySelector('[data-action="redo"]').disabled=locked()||!redo.length;
    root.querySelectorAll('[data-action="add-text"],[data-action="add-line"],[data-action="remove"]').forEach(b=>b.disabled=locked()||busy);
    name.disabled=locked(); $('[data-builtin-note]').hidden=!locked();
    form.querySelectorAll('input,select,textarea').forEach(input=>input.disabled=locked()||!selected);
  }
  function updatePicker() {
    picker.replaceChildren(...templates.map(t=>{ const o=document.createElement('option'); o.value=t.id; o.textContent=t.name+' · '+(t.builtin?s.builtin:s.custom); return o; }));
    if(record) picker.value=record.id;
    const active=templates.find(t=>t.id===data.active.id);
    $('[data-active-name]').textContent=active?.name||data.active.id;
  }
  function propertyPanel() {
    const placeholder=document.createElement('option'); placeholder.value=''; placeholder.textContent=s.choose; placeholder.disabled=true;
    elements.replaceChildren(placeholder,...layout.elements.map(e=>{ const o=document.createElement('option'); o.value=e.id; o.textContent=e.id+' · '+(e.binding||e.text.slice(0,12)||(e.type==='line'?s.line:s.text)); return o; }));
    elements.value=selected||'';
    const e=current();
    form.hidden=!e;
    if(e) for(const input of form.elements) if(input.name) input.value=e[input.name];
    form.querySelectorAll('[data-text-property]').forEach(el=>el.hidden=e?.type==='line');
    controls();
  }
  function drawRegions() {
    regions.replaceChildren(...layout.elements.map(e=>{
      const box=document.createElement('div'); box.className='layout-region'; box.dataset.element=e.id;
      box.classList.toggle('selected',e.id===selected); box.classList.toggle('invalid',issues.some(i=>i.element_id===e.id));
      box.style.left=e.x/1872*100+'%'; box.style.top=e.y/1404*100+'%';
      box.style.width=e.width/1872*100+'%'; box.style.height=e.height/1404*100+'%';
      box.setAttribute('aria-label',e.id);
      const handle=document.createElement('span'); handle.className='layout-handle'; handle.dataset.resize=''; box.append(handle);
      return box;
    }));
  }
  function select(id) { selected=id; propertyPanel(); drawRegions(); }
  function load(id) {
    record=templates.find(t=>t.id===id)||templates[0];
    const draft=drafts.get(record.id); layout=clone(draft?.layout||record.layout); name.value=draft?.name||record.name;
    selected=null; undo=[]; redo=[]; issues=[]; picker.value=record.id; changed(); propertyPanel(); drawRegions(); queue();
  }
  async function request(url,body) {
    const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf},body:JSON.stringify(body)});
    let value; try { value=await response.json(); } catch { throw new Error(s.failed); }
    if(!response.ok) { if(value.issues) setIssues(value.issues); throw new Error(value.error||s.failed); }
    return value;
  }
  function setIssues(next) {
    issues=next; const list=$('[data-issues]'); list.hidden=!issues.length;
    list.replaceChildren(...issues.map(i=>{ const li=document.createElement('li'); li.textContent=i.element_id+'：'+(s[i.code]||s.failed); return li; }));
    drawRegions(); controls();
  }
  function queue() {
    const ticket=++generation; clearTimeout(timer); controller?.abort(); pending=true; controls();
    message(s.loading);
    timer=setTimeout(async()=>{
      controller=new AbortController();
      try {
        const response=await fetch(root.dataset.preview,{method:'POST',credentials:'same-origin',signal:controller.signal,
          headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf},body:JSON.stringify({layout})});
        const value=await response.json(); if(ticket!==generation) return;
        if(!response.ok) throw new Error(value.error||s.failed);
        const bytes=Uint8Array.from(atob(value.png_base64),c=>c.charCodeAt(0));
        const url=URL.createObjectURL(new Blob([bytes],{type:'image/png'}));
        const image=new Image(); image.src=url; await image.decode();
        if(ticket!==generation) { URL.revokeObjectURL(url); return; }
        $('[data-canvas-image]').src=url; if(objectUrl) URL.revokeObjectURL(objectUrl); objectUrl=url;
        setIssues(value.issues); message(issues.length?s.adjust:dirty?s.unsaved:locked()?s.readonly:'',issues.length>0);
      } catch(error) { if(error.name!=='AbortError'&&ticket===generation) { issues=[{element_id:'preview',code:'error'}]; message(error.message,true); } }
      finally { if(ticket===generation) { pending=false; controls(); } }
    },200);
  }
  function clamp(e) {
    e.width=Math.round(Math.max(1,Math.min(1872,e.width))); e.height=Math.round(Math.max(1,Math.min(1404,e.height)));
    e.x=Math.round(Math.max(0,Math.min(1872-e.width,e.x))); e.y=Math.round(Math.max(0,Math.min(1404-e.height,e.y)));
  }
  async function save() {
    if(!dirty) return;
    if(locked()) throw new Error(s.readonly);
    if(!name.value.trim()) throw new Error(s.empty);
    const saved=await request(root.dataset.url+'/'+record.id,{revision:record.revision,name:name.value,layout});
    Object.assign(record,saved); layout=clone(saved.layout); drafts.delete(record.id); dirty=false; updatePicker(); controls(); message(s.saved);
  }
  picker.addEventListener('change',()=>load(picker.value));
  elements.addEventListener('change',()=>select(elements.value));
  name.addEventListener('input',()=>{ changed(); message(s.unsaved); });
  form.addEventListener('submit',event=>event.preventDefault());
  form.addEventListener('focusin',()=>{ if(!locked()) remember(); });
  form.addEventListener('input',event=>{
    const e=current(),input=event.target; if(!e||locked()||!input.name) return;
    if(input.type==='number' && !input.validity.valid) return;
    const numeric=['x','y','width','height','size','gray','weight','line_height'].includes(input.name);
    e[input.name]=numeric?Number(input.value):input.value; clamp(e);
    if(['x','y','width','height'].includes(input.name)) input.value=e[input.name];
    changed(); drawRegions(); queue();
  });
  $('[data-show-regions]').addEventListener('change',event=>canvas.classList.toggle('show-regions',event.target.checked));
  let drag;
  canvas.addEventListener('pointerdown',event=>{
    const box=event.target.closest('[data-element]'); if(!box) { select(null); return; }
    select(box.dataset.element); if(locked()) return;
    event.preventDefault(); canvas.focus(); canvas.setPointerCapture(event.pointerId); remember();
    drag={start:clone(current()),x:event.clientX,y:event.clientY,resize:event.target.hasAttribute('data-resize')};
  });
  canvas.addEventListener('pointermove',event=>{
    if(!drag) return;
    const e=current(),bounds=canvas.getBoundingClientRect();
    const dx=(event.clientX-drag.x)*1872/bounds.width,dy=(event.clientY-drag.y)*1404/bounds.height;
    if(drag.resize) { e.width=Math.round((drag.start.width+dx)/4)*4; e.height=Math.round((drag.start.height+dy)/4)*4; }
    else { e.x=Math.round((drag.start.x+dx)/4)*4; e.y=Math.round((drag.start.y+dy)/4)*4; }
    for(const axis of ['x','y']) {
      const guide=$('[data-guide-'+axis+']'); guide.hidden=true;
      if(!drag.resize) {
        const size=axis==='x'?'width':'height',canvasSize=axis==='x'?1872:1404;
        const anchors=[0,canvasSize/2,canvasSize,...layout.elements.filter(b=>b!==e).flatMap(b=>[b[axis],b[axis]+b[size]])];
        outer: for(const offset of [0,e[size]/2,e[size]]) for(const anchor of anchors) {
          if(Math.abs(e[axis]+offset-anchor)<=4) { e[axis]=anchor-offset; guide.hidden=false; guide.style[axis==='x'?'left':'top']=anchor/canvasSize*100+'%'; break outer; }
        }
      }
    }
    clamp(e); changed(); drawRegions(); propertyPanel(); queue();
  });
  const finishDrag=()=>{ if(drag) { drag=null; $('[data-guide-x]').hidden=$('[data-guide-y]').hidden=true; propertyPanel(); } };
  canvas.addEventListener('pointerup',finishDrag); canvas.addEventListener('pointercancel',finishDrag);
  canvas.addEventListener('keydown',event=>{
    if(locked()||!current()) return;
    const directions={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
    const d=directions[event.key]; if(!d) return;
    event.preventDefault(); remember(); const e=current(),step=event.shiftKey?10:1;
    e.x+=d[0]*step; e.y+=d[1]*step; clamp(e); changed(); propertyPanel(); drawRegions(); queue();
  });
  root.addEventListener('click',async event=>{
    const action=event.target.closest('[data-action]')?.dataset.action; if(!action) return;
    try {
      if(action==='undo'||action==='redo') {
        const from=action==='undo'?undo:redo,to=action==='undo'?redo:undo;
        if(!from.length||locked()) return; to.push(snapshot()); const state=JSON.parse(from.pop()); layout=state.layout; name.value=state.name;
        if(!current()) selected=null; changed(); propertyPanel(); drawRegions(); queue(); return;
      }
      if(action==='add-text'||action==='add-line') {
        if(locked()) return; if(layout.elements.length>=32) throw new Error(s.limit); remember();
        const id='element-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,6);
        layout.elements.push({id,type:action==='add-text'?'text':'line',x:96,y:480,width:800,height:action==='add-text'?100:2,binding:'',text:action==='add-text'?s.newtext:'',prefix:'',suffix:'',font:'sans',weight:400,size:40,line_height:1.35,align:'left',gray:0});
        selected=id; changed(); propertyPanel(); drawRegions(); queue(); return;
      }
      if(action==='remove') {
        if(locked()||!current()) return; if(layout.elements.length===1) throw new Error(s.failed);
        remember(); layout.elements=layout.elements.filter(e=>e.id!==selected); selected=null; changed(); propertyPanel(); drawRegions(); queue(); return;
      }
      busy=true; controls();
      if(action==='copy') {
        const source=clone(layout);
        const copy=await request(root.dataset.url,{source_id:record.id,name:name.value+' · '+s.copy});
        templates.push(copy); drafts.set(copy.id,{layout:{...clone(copy.layout),elements:source.elements},name:copy.name}); updatePicker(); load(copy.id);
      } else if(action==='save') await save();
      else if(action==='apply') {
        await save(); const result=await request(root.dataset.url+'/'+record.id+'/apply',{revision:record.revision});
        data.active=clone(layout); updatePicker(); message(result.message||s.applied); $('.layout-status>span:last-child').textContent=s.pending+' · '+result.content_version;
      } else if(action==='delete') {
        await request(root.dataset.url+'/'+record.id,{action:'delete',revision:record.revision}); drafts.delete(record.id);
        templates.splice(templates.indexOf(record),1); updatePicker(); load(data.active.id);
      }
    } catch(error) { message(error.message,true); }
    finally { busy=false; controls(); }
  });
  updatePicker(); load(data.active.id);
})();
