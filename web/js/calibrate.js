/* =========================================================================
   SAFEX AI — camera ↔ floor-plan calibration.

   Four points, clicked twice: once where you can see them in the camera, once
   where they are on the plan. Corridor corners, a doorway, floor tiles —
   anything flat and identifiable. From those the server fits a homography, and
   from then on the platform can say *where in the building* something is, and
   measure a corridor in metres instead of percentages.

   Pairs are entered one at a time, alternating image → plan, because that is
   the order a person naturally thinks in ("that corner… is here") and it makes
   a mismatched pair impossible to create by accident.
   ========================================================================= */
(() => {
  const modal = document.getElementById('calModal');
  if(!modal) return;
  const shot   = document.getElementById('calShot');
  const planEl = document.getElementById('calPlan');
  const statusEl = document.getElementById('calStatus');
  const listEl = document.getElementById('calPairs');
  const titleEl = document.getElementById('calTitle');
  const saveBtn = document.getElementById('calSave');
  const undoBtn = document.getElementById('calUndo');
  const clearBtn = document.getElementById('calClear');

  const NEEDED = 4;
  let cam = null, layout = null, pairs = [], pending = null;

  function open(camera){
    cam = camera;
    pairs = []; pending = null;
    titleEl.textContent = `${camera.name} · ${camera.zone_name || camera.zone_id}`;
    // a frozen frame, so the points stay where they were clicked
    shot.style.backgroundImage = `url(/api/camera/${camera.id}/frame?t=${Date.now()})`;
    modal.classList.add('on');
    fetch('/api/layout').then(r => r.json()).then(d => { layout = d; drawPlan(); render(); });
  }
  function close(){ modal.classList.remove('on'); }
  window.SAFEX_CALIBRATE = open;

  /* the camera's own floor, with its room highlighted so there is no doubt
     which part of the building you are pointing at */
  function drawPlan(){
    if(!layout) return;
    const rooms = layout.zones.filter(z => z.floor === cam.floor);
    planEl.innerHTML = rooms.map(z => `
      <div class="cal-room${z.id === cam.zone_id ? ' mine' : ''}${z.is_exit ? ' exit' : ''}"
           style="left:${z.rect[0]*100}%;top:${z.rect[1]*100}%;
                  width:${z.rect[2]*100}%;height:${z.rect[3]*100}%">
        <span>${z.name}</span></div>`).join('');
  }

  const pos = (ev, el) => {
    const b = el.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (ev.clientX-b.left)/b.width)),
            Math.max(0, Math.min(1, (ev.clientY-b.top)/b.height))];
  };

  shot.addEventListener('click', ev => {
    if(pairs.length >= NEEDED) return;
    if(pending){ statusEl.textContent = 'Now click the same spot on the plan →'; return; }
    pending = {image: pos(ev, shot)};
    render();
  });

  planEl.addEventListener('click', ev => {
    if(pairs.length >= NEEDED) return;
    if(!pending){ statusEl.textContent = '← Start on the camera view'; return; }
    pending.plan = pos(ev, planEl);
    pairs.push(pending); pending = null;
    render();
  });

  undoBtn.addEventListener('click', () => { pending ? (pending = null) : pairs.pop(); render(); });
  clearBtn.addEventListener('click', () => { pairs = []; pending = null; render(); });
  document.getElementById('calClose').addEventListener('click', close);

  function render(){
    shot.querySelectorAll('.cal-pt').forEach(e => e.remove());
    planEl.querySelectorAll('.cal-pt').forEach(e => e.remove());
    const dot = (host, p, n, cls) => {
      const el = document.createElement('i');
      el.className = 'cal-pt ' + (cls||'');
      el.style.left = p[0]*100+'%'; el.style.top = p[1]*100+'%';
      el.textContent = n;
      host.appendChild(el);
    };
    pairs.forEach((p, i) => { dot(shot, p.image, i+1); dot(planEl, p.plan, i+1); });
    if(pending) dot(shot, pending.image, pairs.length+1, 'pending');

    const left = NEEDED - pairs.length;
    statusEl.textContent = pairs.length >= NEEDED
      ? 'Four pairs ready — save the calibration.'
      : pending
        ? `Point ${pairs.length+1}: now click the same spot on the plan →`
        : `Point ${pairs.length+1} of ${NEEDED}: click a spot you can identify in the camera view`;
    saveBtn.disabled = pairs.length < NEEDED;
    listEl.innerHTML = pairs.map((p,i) =>
      `<div class="cal-pair"><b>${i+1}</b>
         <span>image ${p.image.map(v=>v.toFixed(2)).join(', ')}</span>
         <span>plan ${p.plan.map(v=>v.toFixed(2)).join(', ')}</span></div>`).join('')
      || '<div class="ed-hint">Pick points that are far apart and not in a straight line — '
        + 'the corners of the corridor work well.</div>';
  }

  saveBtn.addEventListener('click', async () => {
    const r = await SAFEX.post(`/api/cameras/${cam.id}/calibration`, {points: pairs});
    if(r.ok){
      SAFEX.toast(`${cam.name} calibrated${r.note ? ' — ' + r.note : ''}`, '⊹');
      close();
    } else {
      statusEl.textContent = '⚠ ' + (r.error || 'could not fit those points');
    }
  });
})();
