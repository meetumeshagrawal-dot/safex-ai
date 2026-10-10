/* =========================================================================
   SAFEX AI — scenario console + Simulation/Live mode toggle.
   ========================================================================= */
(() => {
  const zoneSel=document.getElementById('scZone'), eventBtns=document.getElementById('eventBtns'),
        resetBtn=document.getElementById('resetBtn');
  const ICO={fire:'🔥',gas_leak:'☣',overheat:'🌡',mechanical:'⚙',smoke:'💨'};

  /* rooms grouped by floor — the plant is a three-storey building */
  fetch('/api/layout').then(r=>r.json()).then(d=>{
    const floors=d.floors||[], zones=d.zones.filter(z=>!z.is_exit);
    zoneSel.innerHTML = floors.map(f=>{
      const opts = zones.filter(z=>z.floor===f.id).map(z=>
        `<option value="${z.id}">${z.name}${z.critical?' ★':''}</option>`).join('');
      return opts ? `<optgroup label="${f.name} — ${f.subtitle}">${opts}</optgroup>` : '';
    }).join('');
    const demo=zones.find(z=>z.id==='A6')||zones.find(z=>z.id==='Z7'); if(demo) zoneSel.value=demo.id;
  }).catch(()=>{ zoneSel.innerHTML='<option>Z1</option>'; });

  eventBtns.querySelectorAll('button').forEach(b=>b.addEventListener('click',async ()=>{
    const kind=b.dataset.kind, zone=zoneSel.value, name=zoneSel.options[zoneSel.selectedIndex].text.replace(' ★','');
    const r=await SAFEX.post('/api/scenario',{zone,kind,intensity:1.0});
    if(r.ok){
      SAFEX.toast(`${ICO[kind]} ${r.label} injected · ${name}`,ICO[kind]);
      // show the operator where it happened, even if it's on another level
      if(window.SAFEX_FLOOR) window.SAFEX_FLOOR.show(zone);
    }
    else SAFEX.toast(r.error||'Could not inject event','⚠');
  }));

  resetBtn.addEventListener('click',async ()=>{
    await SAFEX.post('/api/reset'); SAFEX.toast('Plant reset to nominal','↺');
    if(window.speechSynthesis) window.speechSynthesis.cancel();
  });

  /* ---- SIM / LIVE mode toggle ---- */
  const modeSwitch=document.getElementById('modeSwitch');
  modeSwitch.querySelectorAll('button').forEach(b=>b.addEventListener('click',async ()=>{
    const mode=b.dataset.mode;
    if(SAFEX.mode()===mode) return;
    const r=await SAFEX.post('/api/mode',{mode});
    if(r.ok){
      SAFEX.toast(mode==='live'?'◉ Live Camera mode — detections now drive the plant'
                              :'⎔ Simulation mode — scenario console active', mode==='live'?'◉':'⎔');
    } else SAFEX.toast(r.error||'Mode switch failed','⚠');
  }));

  // reflect mode: the scenario console is a DRILL tool — it only exists in
  // simulation mode. In live mode real cameras drive the plant, so injecting
  // fake events would be meaningless (and misleading in a real control room).
  SAFEX.on(s=>{
    const live=(s.meta.mode==='live');
    const card=document.getElementById('consoleCard');
    if(card) card.classList.toggle('mode-hidden', live);
    eventBtns.querySelectorAll('button').forEach(btn=>btn.disabled=live);
    let note=document.getElementById('modeNote');
    if(!note){ note=document.createElement('div'); note.id='modeNote'; note.className='console-note';
      document.querySelector('.console-body').appendChild(note); }
    note.innerHTML = live
      ? '◉ <b style="color:var(--data)">LIVE CAMERA MODE</b> — hazards are driven by real camera detections.'
      : '⎔ <b style="color:var(--accent)">SIMULATION MODE</b> — inject events above to drive a full incident. Switch to ◉ LIVE CAM to use real camera detections.';
  });
})();
