/* =========================================================================
   SAFEX AI — living plant schematic for a MULTI-FLOOR building.

   One grid per floor (Ground / Level 1 / Level 2), a tab strip that keeps
   every level's hazard visible even while you are looking at another one, and
   a canvas overlay that draws: ambient radar sweep, hazard bloom, animated
   evacuation routes, WORKER DOTS streaming toward the stairs and exits, and
   FIRE PROPAGATION ember particles.

   Evacuation routes routinely span floors (Canteen → corridor → Stair A →
   down two levels → East Muster). The overlay draws the part of the route that
   happens on the floor you are watching, and marks the stair landing where the
   crowd leaves or enters this level.
   ========================================================================= */
(() => {
  const stackEl = document.getElementById('floor');
  const tabsEl  = document.getElementById('floorTabs');
  const titleEl = document.getElementById('floorTitle');
  const canvas  = document.getElementById('fxCanvas');
  const ctx = canvas.getContext('2d');
  const drawer = document.getElementById('drawer');
  const drawerInner = document.getElementById('drawerInner');
  const scrim = document.getElementById('scrim');
  const followEl = document.getElementById('followHazard');

  let layout = null;            // {floors:[], zones:[]}
  let cells = {};               // zone_id -> DOM element
  let centers = {};             // zone_id -> {x,y,w,h} on the ACTIVE floor
  let built = false, selected = null;
  let activeFloor = 0, lastAutoJump = 0, userPinned = 0;
  const camByZone = {};         // zone_id -> camera label

  const SENSOR_MAX = {temperature:140, gas:900, smoke:100, vibration:20, noise:130};
  const SENSOR_UNIT = {temperature:'°C', gas:'ppm', smoke:'%', vibration:'mm/s', noise:'dB'};

  const floorOf = id => (layout?.zones.find(z => z.id === id)?.floor ?? 0);
  const floorMeta = f => layout?.floors.find(x => x.id === f);

  /* ---------- build one plan per floor ----------------------------------
     Rooms are rectangles in normalised plan coordinates, positioned absolutely
     over the floor's drawing area. That is what lets an uploaded floor-plan
     photo sit behind them, and what lets the operator draw a room anywhere
     instead of filling a fixed grid.
  --------------------------------------------------------------------- */
  const MARKER_ICON = {extinguisher:'🧯', exit_sign:'🚪', fire_door:'⛨',
                       hydrant:'🚰', alarm_point:'🔔', route:''};

  function build(){
    stackEl.innerHTML = '';
    cells = {};
    Object.keys(painted).forEach(k => delete painted[k]);
    layout.floors.forEach(f => {
      const plan = document.createElement('div');
      plan.className = 'floor-plan' + (f.id === activeFloor ? ' on' : '');
      plan.dataset.floor = f.id;

      if(f.plan_image){
        const bg = document.createElement('img');
        bg.className = 'plan-bg'; bg.src = f.plan_image; bg.alt = '';
        bg.style.opacity = (f.plan_opacity ?? 0.45);
        plan.appendChild(bg);
      }

      layout.zones.filter(z => z.floor === f.id).forEach(z => {
        const cell = document.createElement('div');
        const r = z.rect || [0, 0, 0.15, 0.2];
        cell.style.left   = (r[0]*100) + '%';
        cell.style.top    = (r[1]*100) + '%';
        cell.style.width  = (r[2]*100) + '%';
        cell.style.height = (r[3]*100) + '%';
        if(z.is_exit){
          cell.className = 'zone exit';
          cell.dataset.exit = z.id;
          cell.innerHTML = `<div class="z-exit-label">▣ ${z.name}</div>
            <div class="z-cat">Muster point</div><div class="z-exit-chev">⟶</div>`;
        } else if(z.vertical){
          cell.className = 'zone stair';
          cell.dataset.zone = z.id;
          cell.innerHTML =
            `<div class="z-fill"></div><div class="z-id">${z.id}</div>
             <div class="z-stair-ico">⇅</div>
             <div><div class="z-name">${z.name.split('·')[0].trim()}</div>
                  <div class="z-cat">Stair core</div></div>
             <div class="z-bottom"><div class="z-score">0</div>
               <div class="z-occ">◍ <span>0</span></div></div>`;
          cell.addEventListener('click', () => openDrawer(z.id));
          cells[z.id] = cell;
        } else {
          cell.className = 'zone' + (z.critical ? ' critical-asset' : '');
          cell.dataset.zone = z.id;
          cell.innerHTML =
            `<div class="z-feed-wrap"><img class="z-feed" alt=""></div>
             <div class="z-fill"></div><div class="z-id">${z.id}</div>
             <div class="z-cam"></div>
             <div><div class="z-name">${z.name}</div><div class="z-cat">${z.category}</div></div>
             <div class="z-bottom"><div class="z-score">0</div>
               <div class="z-occ">◍ <span>0</span></div></div>`;
          cell.addEventListener('click', () => openDrawer(z.id));
          cells[z.id] = cell;
        }
        plan.appendChild(cell);
      });

      // fire-safety assets placed on the plan (read-only here; the Site Editor
      // is where they are placed and moved)
      (layout.markers||[]).filter(m => m.floor === f.id).forEach(m => {
        const el = document.createElement('div');
        el.className = 'plan-marker mk-' + m.type;
        el.dataset.marker = m.id;
        el.style.left   = (m.rect[0]*100) + '%';
        el.style.top    = (m.rect[1]*100) + '%';
        el.style.width  = (m.rect[2]*100) + '%';
        el.style.height = (m.rect[3]*100) + '%';
        el.title = (m.label || m.type.replace('_',' ')) + (m.room ? ' · ' + m.room : '');
        el.innerHTML = m.type === 'route'
          ? `<span class="mk-route-lab">${m.label || 'Egress route'}</span>`
          : `<span class="mk-ico">${MARKER_ICON[m.type] || '◆'}</span>`;
        plan.appendChild(el);
      });

      stackEl.appendChild(plan);
    });
    buildTabs();
    built = true;
    remeasure();
    setTimeout(syncFeeds, 300);
  }

  /* ---------- floor tabs ---------- */
  function buildTabs(){
    tabsEl.innerHTML = layout.floors.map(f => `
      <button class="ftab${f.id===activeFloor?' on':''}" data-floor="${f.id}">
        <span class="ft-dot"></span>
        <span class="ft-txt"><b>${f.short}</b><i>${f.subtitle}</i></span>
        <span class="ft-stats"><span class="ft-score">0</span><span class="ft-occ">◍ 0</span></span>
      </button>`).join('');
    tabsEl.querySelectorAll('.ftab').forEach(b => b.addEventListener('click', () => {
      userPinned = Date.now();
      setFloor(+b.dataset.floor);
    }));
  }

  function setFloor(id){
    if(id === activeFloor) return;
    activeFloor = id;
    stackEl.querySelectorAll('.floor-plan').forEach(p =>
      p.classList.toggle('on', +p.dataset.floor === id));
    tabsEl.querySelectorAll('.ftab').forEach(b =>
      b.classList.toggle('on', +b.dataset.floor === id));
    // The tab row scrolls sideways when the levels do not all fit. Auto-follow
    // can switch to a floor whose tab is off to one side, so bring it into
    // view — otherwise the operator is looking at a level the tab strip still
    // shows as somewhere else.
    const activeTab = tabsEl.querySelector('.ftab.on');
    if(activeTab && tabsEl.scrollWidth > tabsEl.clientWidth + 1){
      activeTab.scrollIntoView({behavior:'smooth', block:'nearest', inline:'nearest'});
    }
    const f = floorMeta(id);
    if(f && titleEl) titleEl.textContent = f.name;
    remeasure();
    syncFeeds();
  }
  window.SAFEX_setFloor = setFloor;

  /* ---------- measure canvas + cell centres for the ACTIVE floor ----------
     Called through `remeasure()` rather than a bare rAF: a background tab
     suspends animation frames, so a floor switched while the tab was hidden
     would otherwise come back with a stale canvas. */
  function remeasure(){
    requestAnimationFrame(measure);
    setTimeout(measure, 60);
  }

  function measure(){
    const wrap = canvas.parentElement.getBoundingClientRect();
    if(!wrap.width || !wrap.height) return;
    const dpr = Math.min(window.devicePixelRatio||1, 2);
    canvas.width = wrap.width*dpr; canvas.height = wrap.height*dpr;
    ctx.setTransform(dpr,0,0,dpr,0,0);
    centers = {};
    const plan = stackEl.querySelector(`.floor-plan[data-floor="${activeFloor}"]`);
    if(!plan) return;
    plan.querySelectorAll('[data-zone],[data-exit]').forEach(el => {
      const id = el.dataset.zone || el.dataset.exit;
      const r = el.getBoundingClientRect();
      centers[id] = {x:r.left-wrap.left+r.width/2, y:r.top-wrap.top+r.height/2,
                     w:r.width, h:r.height};
    });
  }

  /* ---------- per-frame DOM update (every floor, so tabs stay honest) ---------- */
  const painted = {};                 // zone_id -> last painted signature
  function paint(state){
    state.zones.forEach(z => {
      const el = cells[z.zone_id]; if(!el) return;
      // 53 rooms x 4 snapshots a second: only touch the DOM for the ones that
      // actually changed, otherwise every tick forces a full style recalc
      const sig = `${z.band}|${z.score.toFixed(0)}|${z.occupancy}|${camByZone[z.zone_id]||''}`;
      if(painted[z.zone_id] === sig) return;
      painted[z.zone_id] = sig;
      const col = SAFEX.BAND_COLOR[z.band] || '#25e08a';
      const base = el.classList.contains('stair') ? 'zone stair' : 'zone';
      const flash = el.classList.contains('flash') ? ' flash' : '';
      el.className = `${base} band-${z.band}` + (z.critical?' critical-asset':'') + flash;
      el.querySelector('.z-fill').style.background = col;
      const sc = el.querySelector('.z-score'); sc.textContent = z.score.toFixed(0); sc.style.color = col;
      el.querySelector('.z-occ span').textContent = z.occupancy;
      el.querySelector('.z-occ').style.opacity = z.occupancy>0 ? .78 : .3;
      const cam = el.querySelector('.z-cam');
      if(cam){
        cam.textContent = camByZone[z.zone_id] ? '◉' : '';
        cam.title = camByZone[z.zone_id] || '';
      }
    });
    paintMarkers(state);
    paintTabs(state);
  }

  /* A drawn egress route is not decoration — it shows whether that corridor is
     usable right now, so the operator sees the blockage on the plan and not
     only in a list. */
  function paintMarkers(state){
    const byRoom = {};
    ((state.compliance||{}).violations||[]).forEach(v => byRoom[v.room] = v.severity);
    stackEl.querySelectorAll('.plan-marker').forEach(el => {
      const m = (layout.markers||[]).find(x => x.id === el.dataset.marker);
      if(!m) return;
      const sev = byRoom[m.room];
      el.classList.toggle('violation', !!sev);
      el.classList.toggle('sev-blocked', sev === 'BLOCKED');
      el.classList.toggle('sev-obstructed', sev === 'OBSTRUCTED');
      el.classList.toggle('sev-narrowed', sev === 'NARROWED');
      if(m.type === 'route'){
        const lab = el.querySelector('.mk-route-lab');
        if(lab) lab.textContent = sev ? `⚠ ${sev}` : (m.label || 'Egress route');
      }
    });
  }

  function paintTabs(state){
    (state.floor_risk||[]).forEach(fr => {
      const tab = tabsEl.querySelector(`.ftab[data-floor="${fr.floor}"]`);
      if(!tab) return;
      const col = SAFEX.BAND_COLOR[fr.band] || '#25e08a';
      tab.querySelector('.ft-dot').style.background = col;
      tab.querySelector('.ft-dot').style.boxShadow = `0 0 10px ${col}`;
      const s = tab.querySelector('.ft-score');
      s.textContent = fr.score.toFixed(0); s.style.color = col;
      tab.querySelector('.ft-occ').textContent = '◍ ' + fr.occupancy;
      tab.classList.toggle('crit', fr.band === 'CRITICAL');
      tab.classList.toggle('high', fr.band === 'HIGH');
      tab.title = fr.worst_name ? `Worst: ${fr.worst_name} (${fr.score})` : '';
    });
    autoFollow(state);
  }

  /* ---------- auto-follow: pull the operator to the level that's burning ---- */
  function autoFollow(state){
    if(!followEl?.checked) return;
    if(Date.now() - userPinned < 12000) return;       // respect a manual choice
    if(Date.now() - lastAutoJump < 4000) return;
    const risk = (state.floor_risk||[]).slice().sort((a,b) => b.score-a.score)[0];
    if(!risk || risk.floor === activeFloor) return;
    const here = (state.floor_risk||[]).find(f => f.floor === activeFloor);
    if(risk.score < 55 || risk.score - (here?.score||0) < 12) return;
    lastAutoJump = Date.now();
    setFloor(risk.floor);
    const f = floorMeta(risk.floor);
    SAFEX.toast(`Jumped to ${f?.name||'level '+risk.floor} — ${risk.worst_name} at ${risk.score}`, '⇅');
  }

  /* ---------- particle systems ---------- */
  const workers = {};   // routeKey -> [{t, lane, spd}]
  const embers = {};    // zone_id -> [{x,y,vy,life,size}]

  function ensureWorkers(key, n){
    if(!workers[key]) workers[key] = [];
    const arr = workers[key];
    while(arr.length < n) arr.push({t: Math.random(), lane:(Math.random()-.5)*10, spd:0.05+Math.random()*0.04});
    while(arr.length > n) arr.pop();
    return arr;
  }

  function polyAt(pts, t){
    if(pts.length<2) return {...pts[0], ang:0};
    const seg = 1/(pts.length-1);
    let i = Math.min(pts.length-2, Math.floor(t/seg));
    const lt = (t - i*seg)/seg;
    const a = pts[i], b = pts[i+1];
    return {x:a.x+(b.x-a.x)*lt, y:a.y+(b.y-a.y)*lt, ang:Math.atan2(b.y-a.y,b.x-a.x)};
  }

  /* Split a route into the runs that happen on the floor being displayed.
     Returns [{ids:[...], enterFrom:floor|null, leaveTo:floor|null}] */
  function segmentsOnFloor(path){
    const out = [];
    let cur = null;
    path.forEach((id, i) => {
      const f = floorOf(id);
      if(f === activeFloor){
        if(!cur){ cur = {ids:[], enterFrom:(i>0?floorOf(path[i-1]):null), leaveTo:null}; out.push(cur); }
        cur.ids.push(id);
      } else if(cur){
        cur.leaveTo = f;
        cur = null;
      }
    });
    return out;
  }

  /* ---------- main animation loop ---------- */
  let sweep = 0, lastFx = 0;
  const FX_INTERVAL = 1000 / 30;     // 30 fps is plenty for the overlay and
                                     // leaves the main thread free for video
  function frame(now){
    requestAnimationFrame(frame);
    if(now && now - lastFx < FX_INTERVAL) return;
    lastFx = now || 0;
    const state = SAFEX.state;
    if(!built || !state){ ctx.clearRect(0,0,canvas.width,canvas.height); return; }
    const W = canvas.parentElement.clientWidth, H = canvas.parentElement.clientHeight;
    ctx.clearRect(0,0,W,H);

    /* radar sweep (ambient) */
    sweep += 0.004; if(sweep>1) sweep-=1;
    const sx = sweep*W;
    const g = ctx.createLinearGradient(sx-60,0,sx+8,0);
    g.addColorStop(0,'rgba(55,236,219,0)'); g.addColorStop(1,'rgba(55,236,219,0.06)');
    ctx.fillStyle = g; ctx.fillRect(sx-60,0,68,H);
    ctx.strokeStyle='rgba(55,236,219,0.18)'; ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(sx,0); ctx.lineTo(sx,H); ctx.stroke();

    /* hazard bloom + fire propagation (active floor only) */
    state.zones.forEach(z=>{
      if(z.floor !== activeFloor) return;
      const c = centers[z.zone_id]; if(!c) return;
      if(z.score > 30){
        const col = SAFEX.BAND_COLOR[z.band];
        const rad = (c.w*0.5) + z.score*0.9;
        const rg = ctx.createRadialGradient(c.x,c.y,4,c.x,c.y,rad);
        const a = Math.min(.5, z.score/220);
        rg.addColorStop(0, hexA(col,a)); rg.addColorStop(1, hexA(col,0));
        ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(c.x,c.y,rad,0,7); ctx.fill();
      }
      const fireish = (z.flame && z.flame>0.15) || (z.band==='CRITICAL' && (z.drivers||[]).some(d=>/fire|flame/i.test(d)));
      if(fireish){
        const arr = embers[z.zone_id] || (embers[z.zone_id]=[]);
        const intensity = Math.min(1, (z.flame||0.6) + z.score/160);
        if(Math.random() < intensity){
          arr.push({x:c.x+(Math.random()-.5)*c.w*0.5, y:c.y+c.h*0.28,
                    vy:-(0.5+Math.random()*1.2), vx:(Math.random()-.5)*0.5,
                    life:1, size:2+Math.random()*3});
        }
        for(let i=arr.length-1;i>=0;i--){
          const p=arr[i]; p.x+=p.vx; p.y+=p.vy; p.vy*=0.99; p.life-=0.018;
          if(p.life<=0){ arr.splice(i,1); continue; }
          const col = p.life>0.5?'255,210,94':'255,110,40';
          ctx.fillStyle=`rgba(${col},${p.life*0.9})`;
          ctx.beginPath(); ctx.arc(p.x,p.y,p.size*p.life,0,7); ctx.fill();
        }
      } else if(embers[z.zone_id]) { embers[z.zone_id].length=0; }
    });

    /* people and objects a CALIBRATED camera has placed on this plan.
       Drawn as hollow marks, deliberately distinct from the simulated worker
       dots on the evacuation routes — one is a real sighting, the other is the
       model's idea of a crowd, and an operator must never confuse the two. */
    const seen = (state.plan_detections || {})[String(activeFloor)] || [];
    if(seen.length){
      const wrapW = W, wrapH = H;
      seen.forEach(d => {
        const x = d.x*wrapW, y = d.y*wrapH;
        const person = d.label === 'person';
        ctx.strokeStyle = person ? 'rgba(122,196,255,0.95)' : 'rgba(255,203,82,0.9)';
        ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.arc(x, y, person ? 5 : 4, 0, 7); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x-7,y); ctx.lineTo(x+7,y);
        ctx.moveTo(x,y-7); ctx.lineTo(x,y+7); ctx.stroke();
      });
      ctx.font = '600 9px JetBrains Mono, monospace';
      ctx.fillStyle = 'rgba(122,196,255,0.85)';
      ctx.fillText(`${seen.length} seen by camera`, 12, H - 12);
    }

    /* evacuation routes + workers (the part of each route on this floor) */
    const routes = state.evacuation?.routes || {};
    const t = performance.now()/1000;
    const liveKeys = new Set();
    Object.entries(routes).forEach(([zid,rt])=>{
      if(rt.blocked || !rt.path || rt.path.length<2) return;
      segmentsOnFloor(rt.path).forEach((seg, si)=>{
        const pts = seg.ids.map(id=>centers[id]).filter(Boolean);
        if(pts.length<2){
          // a single node on this floor (e.g. just the stair landing) — still
          // mark it so the operator sees the crowd passing through
          if(pts.length===1) stairChip(pts[0], seg.enterFrom, seg.leaveTo);
          return;
        }
        ctx.save();
        ctx.setLineDash([7,7]); ctx.lineDashOffset = -(t*26)%14;
        ctx.strokeStyle='rgba(55,236,219,0.55)'; ctx.lineWidth=2.4;
        ctx.shadowColor='rgba(55,236,219,0.7)'; ctx.shadowBlur=6;
        ctx.beginPath(); ctx.moveTo(pts[0].x,pts[0].y);
        for(let i=1;i<pts.length;i++) ctx.lineTo(pts[i].x,pts[i].y);
        ctx.stroke(); ctx.restore();

        const end = pts[pts.length-1];
        if(seg.leaveTo === null && floorOf(seg.ids[seg.ids.length-1]) === activeFloor
           && rt.exit === seg.ids[seg.ids.length-1]){
          ctx.fillStyle='rgba(37,224,138,0.9)';
          ctx.beginPath(); ctx.arc(end.x,end.y,4,0,7); ctx.fill();
        }
        stairChip(end, null, seg.leaveTo);
        stairChip(pts[0], seg.enterFrom, null);

        const key = zid+':'+si;
        liveKeys.add(key);
        const n = Math.min(14, Math.max(1, rt.occupancy||1));
        const arr = ensureWorkers(key, n);
        arr.forEach(w=>{
          w.t += w.spd*0.016*3; if(w.t>1){ w.t=0; }
          const p = polyAt(pts, w.t);
          const nx = Math.cos(p.ang+Math.PI/2)*w.lane, ny = Math.sin(p.ang+Math.PI/2)*w.lane;
          ctx.fillStyle='rgba(55,236,219,0.95)';
          ctx.shadowColor='rgba(55,236,219,0.9)'; ctx.shadowBlur=8;
          ctx.beginPath(); ctx.arc(p.x+nx,p.y+ny,2.6,0,7); ctx.fill(); ctx.shadowBlur=0;
        });
      });
    });
    Object.keys(workers).forEach(k=>{ if(!liveKeys.has(k)) delete workers[k]; });
  }

  /* a small "↓ Ground" / "↑ Level 2" badge drawn on a stair landing */
  function stairChip(c, fromFloor, toFloor){
    const other = (toFloor ?? fromFloor);
    if(other === null || other === undefined || !c) return;
    const down = other < activeFloor;
    const f = floorMeta(other);
    const label = `${down?'↓':'↑'} ${f? f.short : other}`;
    ctx.save();
    ctx.font = '700 10px JetBrains Mono, monospace';
    const w = ctx.measureText(label).width + 12;
    const x = c.x - w/2, y = c.y + (c.h||40)/2 - 16;
    ctx.fillStyle = 'rgba(6,14,26,0.9)';
    ctx.strokeStyle = 'rgba(55,236,219,0.75)'; ctx.lineWidth = 1;
    ctx.beginPath();
    (ctx.roundRect ? ctx.roundRect(x,y,w,15,7) : ctx.rect(x,y,w,15));
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#37ecdb'; ctx.fillText(label, x+6, y+11);
    ctx.restore();
  }

  function hexA(hex,a){
    const h=hex.replace('#',''); const n=parseInt(h,16);
    return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`;
  }

  /* ---------- zone drawer ---------- */
  function openDrawer(id){
    selected=id; drawer.classList.add('on'); scrim.classList.add('on'); fillDrawer(SAFEX.state);
  }
  function closeDrawer(){ selected=null; drawer.classList.remove('on'); scrim.classList.remove('on'); }
  scrim.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e=>{ if(e.key==='Escape'){ closeDrawer();
    document.getElementById('camModal')?.classList.remove('on'); }});

  function routeHtml(state, z){
    const route = state.evacuation.routes[z.zone_id];
    if(!(route && !route.blocked)){
      return z.occupancy>0
        ? `<div class="dw-route" style="color:var(--critical)">⚠ No safe route — egress blocked</div>`
        : `<div class="muted mono" style="font-size:12px">Zone clear · no evacuation active</div>`;
    }
    const hops = route.path.map((id,i) => {
      const f = route.path_floors ? route.path_floors[i] : floorOf(id);
      const prev = i>0 ? (route.path_floors ? route.path_floors[i-1] : floorOf(route.path[i-1])) : f;
      const jump = f !== prev ? `<span class="dw-hop-floor">${f<prev?'↓':'↑'} ${floorMeta(f)?.short||f}</span>` : '';
      return `${jump}<span class="dw-hop">${id}</span>`;
    }).join('<i>→</i>');
    const cross = route.floors_crossed
      ? ` · ${route.floors_crossed} floor${route.floors_crossed>1?'s':''} via stair ${(route.via_stairs||[]).join('/')}`
      : '';
    return `<div class="dw-route">${hops}<br>
      <span class="muted">exit ${route.exit_name||route.exit} · cost ${route.cost}${cross}</span></div>`;
  }

  function fillDrawer(state){
    if(!state) return;
    const z = state.zones.find(x=>x.zone_id===selected); if(!z) return;
    const col = SAFEX.BAND_COLOR[z.band], ink = SAFEX.BAND_INK[z.band];
    const sensors = Object.entries(z.sensors).map(([k,v])=>{
      const max=SENSOR_MAX[k]||100, pct=Math.min(100,(v/max)*100);
      const c = pct>70?'var(--critical)':pct>45?'var(--high)':pct>25?'var(--elevated)':'var(--normal)';
      return `<div class="sensor-row"><div class="sn">${k}</div>
        <div class="sbar"><div class="sfill" style="width:${pct}%;background:${c};color:${c}"></div></div>
        <div class="sval">${v} ${SENSOR_UNIT[k]||''}</div></div>`;
    }).join('');
    const factors=z.factors||{}, fcolors={gas:'#7bd88f',temperature:'#ff8f3f',smoke:'#9aa7c7',fire:'#ff4368',vibration:'#a78bfa',occupancy:'#37ecdb'};
    const total=Object.values(factors).reduce((a,b)=>a+b,0)||1;
    const segs=Object.entries(factors).filter(([,v])=>v>0.1).map(([k,v])=>`<div class="factor-seg" style="width:${(v/total)*100}%;background:${fcolors[k]||'#888'}"></div>`).join('');
    const legend=Object.entries(factors).filter(([,v])=>v>0.1).map(([k,v])=>`<span><i style="background:${fcolors[k]||'#888'}"></i>${k} ${v.toFixed(0)}</span>`).join('');
    const drivers=(z.drivers||[]).length?`<div class="dw-drivers">${z.drivers.map(d=>`<span>${d}</span>`).join('')}</div>`:`<div class="muted mono" style="font-size:12px">No significant hazard drivers</div>`;
    const camBlock = camByZone[z.zone_id]
      ? `<div class="dw-section"><h4>Camera · ${camByZone[z.zone_id]}</h4>
         <div class="dw-camfeed" data-cam-zone="${z.zone_id}"></div></div>` : '';
    const fm = floorMeta(z.floor);

    drawerInner.innerHTML = `
      <button class="dw-close" aria-label="Close">✕</button>
      <div class="dw-name">${z.name}</div>
      <div class="dw-cat">${fm?fm.name:'Floor '+z.floor} · ${z.category}${z.critical?' · critical asset':''} · ${z.zone_id}</div>
      <div class="dw-score" style="color:${col}">${z.score.toFixed(0)}<span style="font-size:18px;color:var(--mut)"> /100</span></div>
      <span class="dw-band" style="background:${col};color:${ink}">${z.band}</span>
      <span class="mono muted" style="margin-left:10px">${z.trend>=0?'+':''}${z.trend}/s · ${z.occupancy} present</span>
      ${camBlock}
      <div class="dw-section"><h4>Hazard drivers</h4>${drivers}</div>
      <div class="dw-section"><h4>Score composition</h4>
        <div class="factor-bar">${segs||'<div class="factor-seg" style="width:100%;background:var(--edge)"></div>'}</div>
        <div class="factor-legend">${legend}</div></div>
      <div class="dw-section"><h4>Live sensor telemetry</h4>${sensors}</div>
      <div class="dw-section"><h4>Evacuation route</h4>${routeHtml(state, z)}</div>`;
    drawerInner.querySelector('.dw-close').addEventListener('click', closeDrawer);
    if(window.SAFEX_CAM) window.SAFEX_CAM.mountFeeds();
  }

  /* ---------- live camera feed inside the room it watches ---------------
     A room with a camera shows what that camera sees, in place on the plan.
     Only the floor being looked at is mounted — the others are display:none,
     so their feeds idle automatically and cost nothing. */
  let feedsOn = true;
  // Below this the tile is a sliver — a video in it is unreadable, and it would
  // still hold one of the browser's six connections. Such rooms keep the camera
  // dot and the room colour; the feed itself goes to the strip instead.
  const MIN_FEED_PX = 34;
  function syncFeeds(){
    if(!built || !window.SAFEX_CAM) return;
    stackEl.querySelectorAll('.floor-plan').forEach(plan => {
      const active = plan.classList.contains('on') && feedsOn;
      plan.querySelectorAll('[data-zone]').forEach(cell => {
        const img = cell.querySelector('.z-feed');
        if(!img) return;
        const cam = window.SAFEX_CAM.cameraFor(cell.dataset.zone);
        const box = active ? cell.getBoundingClientRect() : null;
        const roomy = !!box && box.height >= MIN_FEED_PX && box.width >= MIN_FEED_PX;
        if(active && cam && roomy){
          cell.classList.add('has-feed');
          window.SAFEX_CAM.attach(img, cam.id);
        } else {
          cell.classList.remove('has-feed');
          window.SAFEX_CAM.detach(img);
        }
      });
    });
  }
  const feedToggle = document.getElementById('planFeeds');
  feedToggle?.addEventListener('change', e => {
    feedsOn = e.target.checked;
    syncFeeds();
  });
  setInterval(syncFeeds, 1500);

  /* expose helpers for the other modules */
  window.SAFEX_FLOOR = {
    setCamMap(map){ Object.assign(camByZone, map); },
    show(zoneId){                                   // jump to a zone's floor
      const f = floorOf(zoneId);
      userPinned = Date.now();
      setFloor(f);
      const el = cells[zoneId];
      if(el){ el.classList.add('flash'); setTimeout(()=>el.classList.remove('flash'), 1400); }
    },
    activeFloor: () => activeFloor,
  };

  /* ---------- init ---------- */
  function loadLayout(){
    return fetch('/api/layout').then(r=>r.json()).then(d=>{
      layout = d; built = false;          // rebuild against the new building
      return d;
    });
  }
  loadLayout().catch(()=>{ layout = {floors:[{id:0,name:'Plant Floor',short:'G',subtitle:''}], zones:[], markers:[]}; });
  // the Site Editor calls this after every save
  window.SAFEX_LAYOUT_RELOAD = () => loadLayout();

  function render(state){
    if(!state || !layout) return;
    if(!built) build();
    // self-heal if the canvas never got sized (tab was hidden / late layout)
    const wrap = canvas.parentElement.getBoundingClientRect();
    if(wrap.width && Math.abs(canvas.width - wrap.width*Math.min(devicePixelRatio||1,2)) > 2) measure();
    paint(state);
    if(selected) fillDrawer(state);
  }
  let rz; window.addEventListener('resize', ()=>{ clearTimeout(rz); rz=setTimeout(()=>built&&measure(),120); });
  document.addEventListener('visibilitychange', ()=>{ if(!document.hidden && built) remeasure(); });
  SAFEX.on(render);
  requestAnimationFrame(frame);
})();
