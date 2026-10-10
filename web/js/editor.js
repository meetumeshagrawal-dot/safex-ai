/* =========================================================================
   SAFEX AI — Site Editor.

   The building is data, and this is where it is drawn: add and delete floors,
   upload a floor-plan photo as a backdrop, drag rooms out over it, place fire
   safety assets (extinguishers, exit signs, fire doors, monitored egress
   routes) and wire the stair cores between levels.

   Every change is saved the moment it is made and the running platform refits
   itself within a couple of hundred milliseconds — no restart, no reload. The
   validator runs on every save, so a room with no route to a muster point is
   called out immediately rather than discovered during an evacuation.
   ========================================================================= */
(() => {
  const modal   = document.getElementById('siteEditor');
  if(!modal) return;
  const stage   = document.getElementById('edStage');
  const floorsEl= document.getElementById('edFloors');
  const propsEl = document.getElementById('edProps');
  const problemsEl = document.getElementById('edProblems');
  const titleEl = document.getElementById('edTitle');
  const fileInput = document.getElementById('edPlanFile');

  const CATEGORIES = ['Clinical','Public','Storage','Utilities','Circulation',
                      'Support','Command','Safety','Access','Process','Logistics',
                      'Egress','Vertical'];
  const HAZARDS = ['fire','smoke','gas','temperature','vibration','noise'];
  const MARKERS = [
    ['extinguisher','🧯 Extinguisher'], ['exit_sign','🚪 Exit sign'],
    ['fire_door','⛨ Fire door'], ['hydrant','🚰 Hydrant'],
    ['alarm_point','🔔 Alarm point'], ['route','▭ Egress route'],
  ];

  let site = null, floor = 0, sel = null, selKind = 'room';
  let tool = 'select';        // select | room | <marker type>
  let busy = false;

  /* ------------------------------------------------------------ API helper */
  async function api(path, opts){
    busy = true;
    try{
      const r = await fetch('/api/site' + path, opts);
      const j = await r.json().catch(() => ({ok:false, error:'bad response'}));
      if(!j.ok && j.error) SAFEX.toast(j.error, '⚠');
      return j;
    } finally { busy = false; }
  }
  const post  = (p, b) => api(p, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b||{})});
  const patch = (p, b) => api(p, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(b||{})});
  const del   = (p)    => api(p, {method:'DELETE'});

  /* Reload the site, redraw the editor, and tell the live dashboard that the
     building changed so its map picks up the new rooms. */
  async function refresh(keepSel){
    const r = await fetch('/api/site');
    site = await r.json();
    if(!site.floors.some(f => f.id === floor)) floor = site.floors[0]?.id ?? 0;
    if(!keepSel) sel = null;
    render();
    if(window.SAFEX_LAYOUT_RELOAD) window.SAFEX_LAYOUT_RELOAD();
  }

  /* ------------------------------------------------------------- rendering */
  function render(){
    if(!site) return;
    renderFloors();
    renderStage();
    renderProps();
    renderProblems();
    titleEl.textContent = site.name || 'Site';
  }

  function renderFloors(){
    floorsEl.innerHTML = site.floors.map(f => `
      <div class="ed-floor${f.id===floor?' on':''}" data-floor="${f.id}">
        <div class="ed-floor-main">
          <b>${f.short || 'F'+f.id}</b>
          <span>${f.name}</span>
        </div>
        <div class="ed-floor-meta">${site.rooms.filter(r=>r.floor===f.id).length} rooms${f.plan_image?' · plan':''}</div>
      </div>`).join('') +
      `<button class="ed-add-floor" id="edAddFloor">＋ Add floor</button>`;

    floorsEl.querySelectorAll('.ed-floor').forEach(el =>
      el.addEventListener('click', () => { floor = +el.dataset.floor; sel = null; render(); }));
    floorsEl.querySelector('#edAddFloor').addEventListener('click', async () => {
      const name = prompt('Floor name', `Level ${site.floors.length}`);
      if(name === null) return;
      const r = await post('/floor', {name, short: name.replace(/[^A-Za-z0-9]/g,'').slice(0,3).toUpperCase()});
      if(r.ok){ floor = r.floor.id; await refresh(); SAFEX.toast(`${name} added`, '🏢'); }
    });
  }

  function renderStage(){
    const f = site.floors.find(x => x.id === floor);
    if(!f){ stage.innerHTML = ''; return; }
    const rooms = site.rooms.filter(r => r.floor === floor);
    const marks = site.markers.filter(m => m.floor === floor);

    stage.innerHTML =
      (f.plan_image ? `<img class="ed-plan" src="${f.plan_image}" alt="" style="opacity:${f.plan_opacity ?? .45}">` : '') +
      rooms.map(r => `
        <div class="ed-room${sel===r.id&&selKind==='room'?' sel':''}${r.is_exit?' exit':''}${r.vertical?' stair':''}"
             data-room="${r.id}"
             style="left:${r.rect[0]*100}%;top:${r.rect[1]*100}%;width:${r.rect[2]*100}%;height:${r.rect[3]*100}%">
          <span class="ed-room-name">${r.name}</span>
          <span class="ed-room-id">${r.id}</span>
          <i class="ed-handle" data-handle="${r.id}"></i>
        </div>`).join('') +
      marks.map(m => `
        <div class="ed-marker${sel===m.id&&selKind==='marker'?' sel':''} mk-${m.type}"
             data-marker="${m.id}"
             style="left:${m.rect[0]*100}%;top:${m.rect[1]*100}%;width:${m.rect[2]*100}%;height:${m.rect[3]*100}%">
          <span>${(MARKERS.find(x=>x[0]===m.type)||['','◆'])[1].split(' ')[0]}</span>
          <i class="ed-handle" data-mhandle="${m.id}"></i>
        </div>`).join('');

    stage.querySelectorAll('[data-room]').forEach(el => {
      el.addEventListener('pointerdown', e => startDrag(e, el, 'room'));
    });
    stage.querySelectorAll('[data-marker]').forEach(el => {
      el.addEventListener('pointerdown', e => startDrag(e, el, 'marker'));
    });
  }

  function renderProblems(){
    const ps = site.problems || [];
    problemsEl.className = 'ed-problems' + (ps.length ? ' has' : '');
    problemsEl.innerHTML = ps.length
      ? `<b>${ps.length} issue${ps.length>1?'s':''}</b>` +
        ps.slice(0, 6).map(p => `<div class="ed-problem ${p.level}">${p.msg}</div>`).join('')
      : `<b class="ok">✓ Building is valid</b><div class="ed-problem">Every room has a route to a muster point.</div>`;
  }

  /* --------------------------------------------------------- property panel */
  function renderProps(){
    const f = site.floors.find(x => x.id === floor);
    if(sel && selKind === 'room'){ return renderRoomProps(site.rooms.find(r => r.id === sel)); }
    if(sel && selKind === 'marker'){ return renderMarkerProps(site.markers.find(m => m.id === sel)); }

    // nothing selected: floor-level tools
    propsEl.innerHTML = `
      <div class="ed-sec"><h4>${f ? f.name : 'Floor'}</h4>
        <label>Floor name</label><input id="pfName" value="${f?f.name:''}">
        <label>Tab label</label><input id="pfShort" value="${f?f.short:''}">
        <label>Subtitle</label><input id="pfSub" value="${f?f.subtitle:''}">
        <button class="btn" id="pfSave">Save floor</button>
      </div>
      <div class="ed-sec"><h4>Floor plan image</h4>
        ${f && f.plan_image
          ? `<div class="ed-planrow"><img src="${f.plan_image}" alt="">
               <button class="btn btn-danger" id="pfDropPlan">Remove</button></div>
             <label>Backdrop opacity <span id="pfOpVal">${Math.round((f.plan_opacity??.45)*100)}%</span></label>
             <input type="range" id="pfOpacity" min="5" max="100" value="${Math.round((f.plan_opacity??.45)*100)}">
             <button class="btn" id="pfDetect">✨ Auto-detect rooms</button>
             <div class="ed-hint">Finds enclosed rectangular regions and proposes them.
               Accept the ones that look right, then rename and adjust — no plan reader
               gets every drawing correct, so nothing is added until you say so.</div>`
          : `<button class="btn" id="pfUpload">⬆ Upload floor plan</button>
             <div class="ed-hint">PNG / JPG of the architect's plan. It sits behind the
               rooms as a tracing backdrop.</div>`}
      </div>
      <div class="ed-sec"><h4>Site</h4>
        <label>Site name</label><input id="pSiteName" value="${site.name||''}">
        <label>Organisation</label><input id="pSiteOrg" value="${site.org||''}">
        <button class="btn" id="pSiteSave">Save site</button>
        <label style="margin-top:12px">Load a preset</label>
        <div class="ed-presets">
          <button class="btn" data-preset="hospital">🏥 Hospital</button>
          <button class="btn" data-preset="industrial">🏭 Plant</button>
          <button class="btn" data-preset="blank">▢ Blank</button>
        </div>
        <div class="ed-hint">Replaces the whole building. The current one is backed up first.</div>
      </div>
      ${site.floors.length > 1 ? `<div class="ed-sec danger">
        <button class="btn btn-danger" id="pfDelete">🗑 Delete ${f?f.name:'floor'}</button>
        <div class="ed-hint">Removes the floor with its rooms, markers and cameras.</div>
      </div>` : ''}`;

    const on = (id, ev, fn) => propsEl.querySelector('#'+id)?.addEventListener(ev, fn);
    on('pfSave','click', async () => {
      await patch(`/floor/${floor}`, {
        name: propsEl.querySelector('#pfName').value,
        short: propsEl.querySelector('#pfShort').value,
        subtitle: propsEl.querySelector('#pfSub').value});
      await refresh();
    });
    on('pfOpacity','input', e => {
      propsEl.querySelector('#pfOpVal').textContent = e.target.value + '%';
      const img = stage.querySelector('.ed-plan');
      if(img) img.style.opacity = e.target.value/100;
    });
    on('pfOpacity','change', async e => {
      await patch(`/floor/${floor}`, {plan_opacity: +e.target.value/100});
      await refresh(true);
    });
    on('pfUpload','click', () => fileInput.click());
    on('pfDropPlan','click', async () => { await del(`/floor/${floor}/plan`); await refresh(); });
    on('pfDetect','click', autoDetect);
    on('pfDelete','click', async () => {
      if(!confirm(`Delete ${f.name} with its ${site.rooms.filter(r=>r.floor===floor).length} rooms?`)) return;
      const r = await del(`/floor/${floor}`);
      if(r.ok){ floor = site.floors[0].id; await refresh(); }
    });
    on('pSiteSave','click', async () => {
      await post('/meta', {name: propsEl.querySelector('#pSiteName').value,
                           org: propsEl.querySelector('#pSiteOrg').value});
      await refresh(true);
    });
    propsEl.querySelectorAll('[data-preset]').forEach(b => b.addEventListener('click', async () => {
      if(!confirm(`Replace the whole building with the ${b.dataset.preset} preset?`)) return;
      const r = await post('/preset', {name: b.dataset.preset});
      if(r.ok){ floor = 0; await refresh(); SAFEX.toast(`${b.dataset.preset} site loaded`, '🏢'); }
    }));
  }

  function renderRoomProps(r){
    if(!r) { sel = null; return renderProps(); }
    const others = site.rooms.filter(x => x.id !== r.id);
    propsEl.innerHTML = `
      <div class="ed-sec"><h4>Room · ${r.id}</h4>
        <label>Name</label><input id="prName" value="${r.name}">
        <label>Category</label>
        <select id="prCat">${CATEGORIES.map(c=>`<option ${c===r.category?'selected':''}>${c}</option>`).join('')}</select>
        <label>Typical occupancy</label><input id="prOcc" type="number" min="0" value="${r.base_occupancy}">
        <label>Hazards this room is prone to</label>
        <div class="ed-chips">${HAZARDS.map(h=>`
          <label class="ed-chip${r.hazards.includes(h)?' on':''}">
            <input type="checkbox" value="${h}" ${r.hazards.includes(h)?'checked':''}>${h}</label>`).join('')}</div>
        <div class="ed-flags">
          <label class="ed-chip${r.critical?' on':''}"><input type="checkbox" id="prCrit" ${r.critical?'checked':''}>Critical asset</label>
          <label class="ed-chip${r.is_exit?' on':''}"><input type="checkbox" id="prExit" ${r.is_exit?'checked':''}>Muster point</label>
          <label class="ed-chip${r.vertical?' on':''}"><input type="checkbox" id="prVert" ${r.vertical?'checked':''}>Stair core</label>
        </div>
        <button class="btn" id="prSave">Save room</button>
      </div>
      <div class="ed-sec"><h4>Connections</h4>
        <div class="ed-hint">Rooms that share a wall are connected automatically.
          Add a link to join a stair core to the floor above, or block a pair the
          geometry joined by mistake.</div>
        <div class="ed-neighbours">${(r.neighbors||[]).map(n=>{
          const o = site.rooms.find(x=>x.id===n);
          return `<span class="ed-nb${o&&o.floor!==r.floor?' cross':''}">${o?o.name:n}</span>`;
        }).join('') || '<i class="ed-hint">No connections — this room is sealed off.</i>'}</div>
        <label>Force a link to</label>
        <select id="prLink"><option value="">choose a room…</option>
          ${others.map(o=>`<option value="${o.id}">${o.name} (${site.floors.find(f=>f.id===o.floor)?.short||o.floor})</option>`).join('')}</select>
        <div class="ed-row"><button class="btn" id="prLinkBtn">Link</button>
          <button class="btn" id="prBlockBtn">Wall off</button></div>
      </div>
      <div class="ed-sec danger">
        <button class="btn btn-danger" id="prDelete">🗑 Delete room</button>
      </div>`;

    const q = id => propsEl.querySelector('#'+id);
    propsEl.querySelectorAll('.ed-chip input').forEach(i =>
      i.addEventListener('change', e => e.target.closest('.ed-chip').classList.toggle('on', e.target.checked)));
    q('prSave').addEventListener('click', async () => {
      await patch(`/room/${r.id}`, {
        name: q('prName').value, category: q('prCat').value,
        base_occupancy: +q('prOcc').value,
        hazards: [...propsEl.querySelectorAll('.ed-chips input:checked')].map(i=>i.value),
        critical: q('prCrit').checked, is_exit: q('prExit').checked,
        vertical: q('prVert').checked});
      await refresh(true);
      SAFEX.toast(`${q('prName').value} saved`, '✓');
    });
    const linkAs = async mode => {
      const other = q('prLink').value;
      if(!other) return;
      await post(`/room/${r.id}/link`, {other, mode});
      await refresh(true);
    };
    q('prLinkBtn').addEventListener('click', () => linkAs('link'));
    q('prBlockBtn').addEventListener('click', () => linkAs('block'));
    q('prDelete').addEventListener('click', async () => {
      if(!confirm(`Delete ${r.name}? Cameras in it are removed too.`)) return;
      const res = await del(`/room/${r.id}`);
      if(res.ok){ sel = null; await refresh(); }
    });
  }

  function renderMarkerProps(m){
    if(!m){ sel = null; return renderProps(); }
    const rooms = site.rooms.filter(r => r.floor === m.floor);
    propsEl.innerHTML = `
      <div class="ed-sec"><h4>${(MARKERS.find(x=>x[0]===m.type)||['',m.type])[1]}</h4>
        <label>Label</label><input id="pmLabel" value="${m.label||''}">
        <label>Room it belongs to</label>
        <select id="pmRoom"><option value="">— none —</option>
          ${rooms.map(r=>`<option value="${r.id}" ${r.id===m.room?'selected':''}>${r.name}</option>`).join('')}</select>
        <button class="btn" id="pmSave">Save</button>
        <div class="ed-hint">${m.type === 'route'
          ? 'Cameras watching this room check this corridor for obstructions — anything parked in it is a blocked-egress violation.'
          : m.type === 'fire_door'
          ? 'Monitored for open/closed status; an open fire door is a compliance violation.'
          : 'Its presence and the clear space around it are checked against the room camera.'}</div>
      </div>
      <div class="ed-sec danger">
        <button class="btn btn-danger" id="pmDelete">🗑 Delete marker</button>
      </div>`;
    propsEl.querySelector('#pmSave').addEventListener('click', async () => {
      await patch(`/marker/${m.id}`, {label: propsEl.querySelector('#pmLabel').value,
                                      room: propsEl.querySelector('#pmRoom').value});
      await refresh(true);
    });
    propsEl.querySelector('#pmDelete').addEventListener('click', async () => {
      const res = await del(`/marker/${m.id}`);
      if(res.ok){ sel = null; await refresh(); }
    });
  }

  /* ------------------------------------------------------- drawing + drag */
  const stageRect = () => stage.getBoundingClientRect();
  const toPlan = (px, py) => {
    const b = stageRect();
    return [Math.max(0, Math.min(1, (px - b.left) / b.width)),
            Math.max(0, Math.min(1, (py - b.top) / b.height))];
  };

  function startDrag(e, el, kind){
    if(tool !== 'select'){ return; }          // drawing takes priority
    e.stopPropagation();
    const id = el.dataset.room || el.dataset.marker;
    sel = id; selKind = kind; render();
    const target = stage.querySelector(kind === 'room' ? `[data-room="${id}"]` : `[data-marker="${id}"]`);
    if(!target) return;

    const resizing = e.target.classList.contains('ed-handle');
    const b = stageRect(), r = target.getBoundingClientRect();
    const start = {x:e.clientX, y:e.clientY,
                   x0:(r.left-b.left)/b.width, y0:(r.top-b.top)/b.height,
                   w0:r.width/b.width, h0:r.height/b.height};
    target.setPointerCapture?.(e.pointerId);

    const move = ev => {
      const dx = (ev.clientX-start.x)/b.width, dy = (ev.clientY-start.y)/b.height;
      let rect;
      if(resizing){
        rect = [start.x0, start.y0, Math.max(0.02, start.w0+dx), Math.max(0.02, start.h0+dy)];
      } else {
        rect = [Math.max(0, Math.min(1-start.w0, start.x0+dx)),
                Math.max(0, Math.min(1-start.h0, start.y0+dy)), start.w0, start.h0];
      }
      target.style.left = rect[0]*100+'%'; target.style.top = rect[1]*100+'%';
      target.style.width = rect[2]*100+'%'; target.style.height = rect[3]*100+'%';
      target._rect = rect;
    };
    const up = async () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      if(!target._rect) return;
      const rect = target._rect.map(v => Math.round(v*1000)/1000);
      target._rect = null;
      await patch(`/${kind}/${id}`, {rect});
      await refresh(true);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  // draw a new room / marker by dragging on empty plan
  stage.addEventListener('pointerdown', e => {
    if(tool === 'select'){ sel = null; render(); return; }
    const [x0, y0] = toPlan(e.clientX, e.clientY);
    const ghost = document.createElement('div');
    ghost.className = 'ed-ghost';
    stage.appendChild(ghost);
    const draw = ev => {
      const [x1, y1] = toPlan(ev.clientX, ev.clientY);
      const r = [Math.min(x0,x1), Math.min(y0,y1), Math.abs(x1-x0), Math.abs(y1-y0)];
      ghost.style.left=r[0]*100+'%'; ghost.style.top=r[1]*100+'%';
      ghost.style.width=r[2]*100+'%'; ghost.style.height=r[3]*100+'%';
      ghost._rect = r;
    };
    const done = async ev => {
      window.removeEventListener('pointermove', draw);
      window.removeEventListener('pointerup', done);
      const r = ghost._rect || [x0, y0, 0.12, 0.14];
      ghost.remove();
      if(r[2] < 0.02 || r[3] < 0.02){ r[2] = Math.max(r[2], 0.12); r[3] = Math.max(r[3], 0.14); }
      if(tool === 'room'){
        const name = prompt('Room name', 'New Room');
        if(name === null) { setTool('select'); return; }
        const res = await post('/room', {floor, rect:r, name, category:'Support'});
        if(res.ok){ sel = res.room.id; selKind='room'; }
      } else {
        const res = await post('/marker', {floor, type: tool, rect: r});
        if(res.ok){ sel = res.marker.id; selKind='marker'; }
      }
      setTool('select');
      await refresh(true);
    };
    window.addEventListener('pointermove', draw);
    window.addEventListener('pointerup', done);
  });

  /* ------------------------------------------------------------ plan upload */
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    if(!file) return;
    if(file.size > 8*1024*1024){ SAFEX.toast('Floor plan must be under 8 MB', '⚠'); return; }
    SAFEX.toast('Uploading floor plan…', '⬆');
    const data = await new Promise(res => {
      const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(file);
    });
    const r = await post(`/floor/${floor}/plan`, {data, filename: file.name});
    fileInput.value = '';
    if(r.ok){ await refresh(true); SAFEX.toast('Floor plan attached', '🗺'); }
  });

  async function autoDetect(){
    SAFEX.toast('Reading the plan…', '✨');
    const r = await post(`/floor/${floor}/autodetect`, {});
    if(!r.ok) return;
    if(!r.rects.length){ SAFEX.toast('No clear rooms found — draw them by hand', '◇'); return; }
    showSuggestions(r.rects);
  }

  /* Suggestions are proposals: they are drawn over the plan and only become
     rooms for the ones the operator accepts. */
  function showSuggestions(rects){
    stage.querySelectorAll('.ed-suggest').forEach(e => e.remove());
    rects.forEach((s, i) => {
      const el = document.createElement('div');
      el.className = 'ed-suggest';
      el.style.left=s.rect[0]*100+'%'; el.style.top=s.rect[1]*100+'%';
      el.style.width=s.rect[2]*100+'%'; el.style.height=s.rect[3]*100+'%';
      el.innerHTML = `<span>＋ room ${i+1}</span>`;
      el.addEventListener('click', async ev => {
        ev.stopPropagation();
        const name = prompt('Room name', `Room ${i+1}`);
        if(name === null) return;
        const res = await post('/room', {floor, rect: s.rect, name});
        el.remove();
        if(res.ok) await refresh(true);
      });
      stage.appendChild(el);
    });
    SAFEX.toast(`${rects.length} rooms proposed — click the ones to keep`, '✨');
  }

  /* ------------------------------------------------------------------ tools */
  function setTool(t){
    tool = t;
    document.querySelectorAll('#edTools [data-tool]').forEach(b =>
      b.classList.toggle('on', b.dataset.tool === t));
    stage.classList.toggle('drawing', t !== 'select');
  }
  document.getElementById('edTools').innerHTML =
    `<button class="ed-tool on" data-tool="select">✥ Select</button>
     <button class="ed-tool" data-tool="room">▭ Draw room</button>` +
    MARKERS.map(([t,l]) => `<button class="ed-tool" data-tool="${t}">${l}</button>`).join('');
  document.querySelectorAll('#edTools [data-tool]').forEach(b =>
    b.addEventListener('click', () => setTool(b.dataset.tool)));

  /* ------------------------------------------------------------ open / close */
  function open(){ modal.classList.add('on'); refresh(); }
  function close(){ modal.classList.remove('on'); }
  document.getElementById('siteEditorBtn')?.addEventListener('click', open);
  document.getElementById('edClose').addEventListener('click', close);
  document.addEventListener('keydown', e => {
    if(!modal.classList.contains('on')) return;
    if(e.key === 'Escape'){ if(tool!=='select') setTool('select'); else close(); }
    if(e.key === 'Delete' && sel){
      const path = selKind === 'room' ? `/room/${sel}` : `/marker/${sel}`;
      if(confirm('Delete the selected item?')) del(path).then(() => { sel=null; refresh(); });
    }
  });
})();
