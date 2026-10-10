/* =========================================================================
   SAFEX AI — instrument panels: KPI cards, radial plant gauge, hazard trend,
   predictive watchlist, smart alerts (+voice), incident timeline, sys status.
   ========================================================================= */
(() => {
  const GAUGE_LEN = 251.3; // semicircle arc length (π·80)

  /* ---- KPI cards ---- */
  const KPI = [
    {k:'safety_index', label:'Safety Index', unit:'/100', ico:'⛨', good:'high'},
    {k:'active_incidents', label:'Active Incidents', unit:'', ico:'◭', good:'lowbad'},
    {k:'people_at_risk', label:'People at Risk', unit:'', ico:'◍', good:'lowbad'},
    {k:'workers_protected', label:'Workers Safe', unit:'', ico:'✓'},
    {k:'mttr_s', label:'Avg Response', unit:'s', ico:'⧗'},
    {k:'monitoring_uptime_s', label:'Uptime', unit:'s', ico:'◷'},
  ];
  const kpiEl = document.getElementById('kpis'); let kpiBuilt=false;
  function buildKpis(){
    kpiEl.innerHTML = KPI.map(d=>`
      <div class="kpi" data-k="${d.k}">
        <div class="kpi-top"><span class="kpi-ico">${d.ico}</span></div>
        <div><span class="kpi-val">0</span><span class="kpi-unit">${d.unit}</span></div>
        <div class="kpi-label">${d.label}</div></div>`).join('');
    kpiBuilt=true;
  }
  function renderKpis(k){
    if(!kpiBuilt) buildKpis();
    KPI.forEach(d=>{
      const box=kpiEl.querySelector(`[data-k="${d.k}"]`), val=k[d.k]??0;
      SAFEX.tween(box.querySelector('.kpi-val'), val, (d.k==='safety_index'||d.k==='mttr_s')?1:0);
      box.classList.remove('warn','bad');
      if(d.good==='high' && val<55) box.classList.add(val<35?'bad':'warn');
      if(d.good==='lowbad' && val>0) box.classList.add(val>=3?'bad':'warn');
    });
  }

  /* ---- radial plant gauge ---- */
  const arc=document.getElementById('plantArc'), scoreEl=document.getElementById('plantScore'),
        bandEl=document.getElementById('plantBand');
  function renderGauge(p){
    SAFEX.tween(scoreEl, p.score, 1);
    const pct=Math.min(1,p.score/100);
    arc.style.strokeDashoffset = GAUGE_LEN*(1-pct);
    arc.style.stroke = p.color;
    scoreEl.style.color = p.color;
    bandEl.textContent = p.band==='NORMAL'?'NOMINAL':p.band;
    bandEl.style.color = p.color;
  }

  /* ---- detection-model readiness ----------------------------------------
     Loading torch and the models takes tens of seconds on a cold start, and it
     runs in the background so the dashboard is usable immediately. Without
     this chip that gap is invisible: a camera added during it looks perfectly
     healthy and simply never detects anything.
  --------------------------------------------------------------------- */
  function renderVision(meta){
    const chip = document.getElementById('visionChip');
    const txt = document.getElementById('visionText');
    if(!chip || !txt) return;
    const v = (meta && meta.vision) || {status:'idle'};
    chip.classList.remove('ready','loading','failed');
    if(v.status === 'ready'){
      chip.classList.add('ready');
      txt.textContent = 'VISION READY';
      chip.title = v.detail || 'Detection models loaded';
    } else if(v.status === 'loading'){
      chip.classList.add('loading');
      txt.textContent = `VISION LOADING ${Math.round(v.seconds||0)}s`;
      chip.title = 'Detection models are still loading — a camera added now '
                 + 'will show video but will not detect yet.';
    } else if(v.status === 'failed'){
      chip.classList.add('failed');
      txt.textContent = 'VISION FAILED';
      chip.title = v.detail || 'Detection models could not be loaded';
    } else {
      txt.textContent = 'VISION IDLE';
      chip.title = 'Models load when the first camera needs them';
    }
  }

  /* ---- sys status chip + mode reflect ---- */
  function renderStatus(state){
    const p=state.plant, sys=document.getElementById('sysStatus');
    sys.classList.remove('warn','crit');
    let txt='NOMINAL';
    if(p.band==='CRITICAL'){ sys.classList.add('crit'); txt='CRITICAL'; }
    else if(p.band==='HIGH'){ sys.classList.add('crit'); txt='ALARM'; }
    else if(p.band==='ELEVATED'){ sys.classList.add('warn'); txt='ELEVATED'; }
    sys.querySelector('span:last-child').textContent = txt;
    document.getElementById('orgName').textContent = state.meta.org || 'Industrial Safety Intelligence';
    // reflect mode
    const mode=state.meta.mode||'simulation';
    document.querySelectorAll('#modeSwitch button').forEach(b=>
      b.classList.toggle('active', b.dataset.mode===mode));
  }

  /* ---- trend chart ---- */
  let chart=null;
  function ensureChart(){
    if(chart||!window.Chart) return;
    chart=new Chart(document.getElementById('trendChart'),{
      type:'line',
      data:{labels:[],datasets:[{data:[],borderColor:'#37ecdb',borderWidth:2,tension:.35,pointRadius:0,fill:true,
        backgroundColor:(c)=>{const{ctx,chartArea}=c.chart;if(!chartArea)return'rgba(55,236,219,.1)';
          const g=ctx.createLinearGradient(0,chartArea.top,0,chartArea.bottom);
          g.addColorStop(0,'rgba(55,236,219,.3)');g.addColorStop(1,'rgba(55,236,219,0)');return g;}}]},
      options:{responsive:true,maintainAspectRatio:false,animation:false,resizeDelay:120,
        layout:{padding:0},
        plugins:{legend:{display:false},tooltip:{enabled:false}},
        scales:{y:{min:0,max:100,grid:{color:'rgba(126,164,224,.07)'},
          ticks:{color:'#5b6c92',font:{size:9,family:'JetBrains Mono'},stepSize:25}},x:{display:false}}}});
  }
  let lastTrend=0;
  function renderTrend(hist,p){
    ensureChart(); if(!chart) return;
    document.getElementById('trendNow').textContent=`▮ ${p.score.toFixed(1)}`;
    // the history itself only advances once a second — redrawing the chart on
    // every snapshot is pure cost
    const now=performance.now();
    if(now-lastTrend < 900) return;
    lastTrend=now;
    const data=hist.slice(-60);
    chart.data.labels=data.map((_,i)=>i);
    chart.data.datasets[0].data=data;
    chart.data.datasets[0].borderColor=p.color;
    chart.update('none');
  }

  /* ---- watchlist ---- */
  let wlSig='';
  function renderWatchlist(wl){
    const el=document.getElementById('watchlist');
    const sig=JSON.stringify(wl||[]);
    if(sig===wlSig) return;
    wlSig=sig;
    if(!wl||!wl.length){ el.innerHTML=`<div class="empty">◇ No zones trending into danger</div>`; return; }
    el.innerHTML=wl.map(w=>{
      const eta = w.eta_ticks && w.eta_ticks<999 ? `ETA ~${w.eta_ticks}s to alarm` : 'rising';
      return `<div class="wl-row"><div style="flex:1.1">
        <div class="wl-name">${w.zone_name}</div><div class="wl-eta">${eta}</div></div>
        <div class="wl-bar"><div class="wl-cur" style="width:${w.current}%"></div>
          <div class="wl-pred" style="left:${Math.min(99,w.predicted)}%"></div></div>
        <div class="wl-delta">▲${w.delta}</div></div>`;
    }).join('');
  }

  /* ---- alerts + voice ---- */
  const seen=new Set();
  let alertSig='';
  function renderAlerts(alerts,fresh){
    const el=document.getElementById('alerts');
    document.getElementById('alertCount').textContent=alerts.length;
    // rebuilding this list on every snapshot is a visible main-thread hitch at
    // 4 Hz — the markup only changes when the alerts themselves do
    const sig=alerts.map(a=>a.id).join('|');
    if(sig===alertSig){ (fresh||[]).forEach(a=>{ if(a.speak && !seen.has(a.id)){ seen.add(a.id); speak(a.voice||a.title); }}); return; }
    alertSig=sig;
    el.innerHTML = alerts.length ? alerts.map(a=>{
      const t=new Date(a.t*1000).toLocaleTimeString('en-GB');
      return `<div class="alert ${a.severity}"><div class="alert-body">
        <div class="alert-title">${a.title}</div><div class="alert-action">${a.action}</div></div>
        <div class="alert-time">${t}</div></div>`;
    }).join('') : `<div class="empty">◇ All systems nominal</div>`;
    (fresh||[]).forEach(a=>{ if(a.speak && !seen.has(a.id)){ seen.add(a.id); speak(a.voice||a.title); }});
  }
  function speak(text){
    if(!document.getElementById('voiceToggle')?.checked || !window.speechSynthesis) return;
    const u=new SpeechSynthesisUtterance(text.replace(/[🔥⚠️#*◉☣]/g,''));
    u.rate=0.98;u.pitch=.95;u.volume=1; window.speechSynthesis.cancel(); window.speechSynthesis.speak(u);
  }
  window.SAFEX_speak=speak;

  /* ---- PA-system repeat: keep announcing evacuation while a zone is on fire ---- */
  let evacText=null;
  function evacAnnouncement(s){
    // worst hazardous zone with people still inside
    const cand=s.zones
      .filter(z=>(z.band==='CRITICAL'||z.band==='HIGH') && z.occupancy>0)
      .sort((a,b)=>b.score-a.score)[0];
    if(!cand) return null;
    const r=(s.evacuation.routes||{})[cand.zone_id];
    if(r && !r.blocked && (r.exit_name||r.exit)){
      const ex=r.exit_name||r.exit;
      return `Attention. Emergency in ${cand.name}. ${cand.occupancy} personnel, `
           + `evacuate immediately. Nearest safe exit is the ${ex}. Move there now and clear the zone.`;
    }
    return `Attention. Emergency in ${cand.name}. Evacuate the area immediately.`;
  }
  setInterval(()=>{
    if(!document.getElementById('voiceToggle')?.checked) return;
    if(evacText && !window.speechSynthesis?.speaking) speak(evacText);
  }, 14000);

  /* ---- incident timeline ---- */
  const open=new Set();
  let incSig='';
  function renderIncidents(inc){
    const el=document.getElementById('incidents');
    document.getElementById('incCount').textContent=`${inc.active_count} active`;
    const active=inc.active||[], hist=inc.history||[];
    // timelines grow one entry at a time; re-rendering every card 4x a second
    // is what produced the long tasks that made the whole page stutter
    const sig=[...active,...hist].map(i=>`${i.id}:${i.status}:${i.severity}:${i.timeline.length}:${i.peak_score}`).join('|');
    if(sig===incSig) return;
    incSig=sig;
    if(!active.length && !hist.length){ el.innerHTML=`<div class="empty">◇ No incidents logged</div>`; return; }
    const card=(i,live)=>{
      const tl=i.timeline.map(e=>`<div class="tl-ev ${e.kind}"><span class="tl-time">${(e.time||'').split(' ')[1]||''}</span> <b>${e.kind}</b> — ${e.message}</div>`).join('');
      const opened=open.has(i.id)?' open':'';
      const acts=live?`<div class="inc-actions">
          <button class="respond" data-act="respond" data-zone="${i.zone_id}">Deploy response</button>
          <button class="report" data-act="report" data-id="${i.id}">Report</button>
          <button class="resolve" data-act="resolve" data-zone="${i.zone_id}">Resolve</button></div>`
        :`<div class="inc-actions"><button class="report" data-act="report" data-id="${i.id}">Report</button></div>`;
      return `<div class="inc${opened}${live?' sev-live':''}" data-id="${i.id}">
        <div class="inc-top" data-toggle="${i.id}"><span class="inc-id">${i.id}</span>
          <span class="inc-sev sev-${i.severity}">${i.severity}</span></div>
        <div class="inc-zone">${i.zone_name}</div>
        <div class="inc-meta">${i.hazard_kind} · peak ${i.peak_score} · ${i.status}${live?'':` · ${i.duration_s}s`}</div>
        <div class="inc-timeline">${tl}</div>${acts}</div>`;
    };
    el.innerHTML=active.map(i=>card(i,true)).join('')+hist.map(i=>card(i,false)).join('');
    el.querySelectorAll('[data-toggle]').forEach(h=>h.addEventListener('click',()=>{
      const id=h.dataset.toggle; open.has(id)?open.delete(id):open.add(id); h.closest('.inc').classList.toggle('open');}));
    el.querySelectorAll('[data-act]').forEach(b=>b.addEventListener('click',async e=>{
      e.stopPropagation(); const act=b.dataset.act;
      if(act==='respond'){ await SAFEX.post('/api/respond',{zone:b.dataset.zone}); SAFEX.toast('Emergency response deployed','🚒'); }
      if(act==='resolve'){ await SAFEX.post('/api/resolve',{zone:b.dataset.zone}); SAFEX.toast('Incident resolved','✅'); }
      if(act==='report'){ window.open('/api/report/'+b.dataset.id,'_blank'); }}));
  }

  /* ---- fire-safety compliance ----------------------------------------
     Deliberately its own panel rather than mixed into the alert feed: a
     blocked fire exit is not an emergency happening now, it is a building
     that would fail an inspection — a different job, for a different person,
     on a different clock.
  -------------------------------------------------------------------- */
  const SEV_COLOR = {NARROWED:'var(--elevated)', OBSTRUCTED:'var(--high)',
                     BLOCKED:'var(--critical)'};
  const BAND_COLOR = {COMPLIANT:'var(--normal)', MINOR:'var(--elevated)',
                      MAJOR:'var(--high)', CRITICAL:'var(--critical)'};
  let compSig='';
  function renderCompliance(c){
    if(!c) return;
    const band=document.getElementById('compBand'), score=document.getElementById('compScore');
    if(!band) return;
    band.textContent=c.band; band.style.color=BAND_COLOR[c.band]||'var(--mut)';
    band.style.borderColor=BAND_COLOR[c.band]||'var(--edge)';
    SAFEX.tween(score, c.score, 0);
    score.style.color=BAND_COLOR[c.band]||'var(--ink)';

    const sig=JSON.stringify([c.by_floor.map(f=>f.score),
                              c.violations.map(v=>v.key+v.severity)]);
    if(sig===compSig) return;
    compSig=sig;

    document.getElementById('compFloors').innerHTML=(c.by_floor||[]).map(f=>`
      <div class="comp-floor" title="${f.name}: ${f.violations} violation(s)">
        <span class="cf-name">${f.name}</span>
        <span class="cf-bar"><i style="width:${f.score}%;background:${BAND_COLOR[f.band]}"></i></span>
        <span class="cf-score" style="color:${BAND_COLOR[f.band]}">${f.score}</span>
      </div>`).join('');

    const el=document.getElementById('compViolations');
    el.innerHTML = c.violations.length ? c.violations.map(v=>{
      const mins = v.duration_s>=60 ? `${Math.round(v.duration_s/60)} min` : `${Math.round(v.duration_s)}s`;
      const objs = (v.objects||[]).map(o=>o.label).join(', ');
      return `<div class="cv-row" data-room="${v.room}">
        <div class="cv-sev" style="background:${SEV_COLOR[v.severity]}"></div>
        <div class="cv-body">
          <div class="cv-title">${v.room_name}<span class="cv-tag"
            style="color:${SEV_COLOR[v.severity]}">${v.severity}</span></div>
          <div class="cv-detail">${v.detail}${objs?` · ${objs}`:''}</div>
          <div class="cv-meta">${v.summary||''} · open ${mins} · ${v.camera_name||v.camera}</div>
        </div></div>`;
    }).join('') : `<div class="empty">◇ Every escape route is clear</div>`;
    el.querySelectorAll('.cv-row').forEach(r=>r.addEventListener('click',()=>{
      if(window.SAFEX_FLOOR) window.SAFEX_FLOOR.show(r.dataset.room);
    }));
  }

  /* ---- automated actions -----------------------------------------------
     Deliberately visible on the main console rather than buried in a settings
     page: automation an operator cannot see is automation they cannot
     supervise. The arming switch, what would fire, and the boundary against
     the building's certified systems all sit together.
  --------------------------------------------------------------------- */
  let actSig = '';
  function renderActions(a){
    const list = document.getElementById('actList');
    if(!list || !a) return;
    const arm = document.getElementById('actArm');
    if(arm && arm.checked !== !!a.armed) arm.checked = !!a.armed;
    const bnd = document.getElementById('actBoundary');
    if(bnd && a.boundary && !bnd.textContent) bnd.textContent = a.boundary;

    const sig = JSON.stringify([a.armed, (a.suggested||[]).map(x=>x.action+x.zone+x.active),
                                (a.active||[]).length]);
    if(sig === actSig) return;
    actSig = sig;

    const active = (a.active||[]).map(r =>
      `<div class="act-row done"><b>✓ ${r.action.replace(/_/g,' ')}</b>
         <span>${r.zone_name||'site'} · ${r.time} · ${r.reason}</span></div>`).join('');

    const sug = (a.suggested||[]).filter(x=>!x.active).slice(0,4).map(x =>
      `<div class="act-row">
         <div class="act-txt"><b>${x.label}</b>
           <span>${x.why}</span>
           <i>${x.device} · ${x.fail_safe}</i></div>
         <button class="btn" data-act-run="${x.action}" data-act-zone="${x.zone}"
           data-confirm="${x.needs_confirm?1:''}">${x.auto?'Run now':'Run'}</button>
       </div>`).join('');

    list.innerHTML = active + sug ||
      `<div class="empty">◇ Nothing to act on</div>`;

    list.querySelectorAll('[data-act-run]').forEach(b => b.addEventListener('click', async () => {
      const body = {action:b.dataset.actRun, zone:b.dataset.actZone};
      let r = await SAFEX.post('/api/actions/run', body);
      if(r.status === 'confirm'){
        if(!confirm(`${r.label}

${r.device}
${r.fail_safe}

Proceed?`)) return;
        r = await SAFEX.post('/api/actions/run', {...body, confirmed:true});
      }
      SAFEX.toast(r.ok ? `${r.label} — done` : (r.error||'refused'), r.ok?'⚡':'⚠');
      actSig = '';
    }));
  }
  document.getElementById('actArm')?.addEventListener('change', async e => {
    const r = await SAFEX.post('/api/actions/arm', {armed: e.target.checked});
    SAFEX.toast(r.armed ? 'Automated actions ARMED' : 'Automated actions disarmed',
                r.armed ? '⚡' : '○');
    actSig = '';
  });
  document.getElementById('compReportBtn')?.addEventListener('click', () => {
    window.open('/api/compliance/report?period=week', '_blank');
  });

  SAFEX.on(s=>{
    renderStatus(s); renderVision(s.meta); renderGauge(s.plant); renderKpis(s.kpis);
    renderCompliance(s.compliance); renderActions(s.actions);
    renderTrend(s.plant_history||[], s.plant);
    renderWatchlist(s.watchlist); renderAlerts(s.alerts, s.new_alerts); renderIncidents(s.incidents);
    evacText = evacAnnouncement(s);   // drives the PA-system repeat
  });
})();
