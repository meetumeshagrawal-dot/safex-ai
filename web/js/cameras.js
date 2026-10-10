/* =========================================================================
   SAFEX AI — camera layer. Live-feed strip, full Camera Monitor grid with
   per-camera configuration (map a real USB / IP / RTSP camera to any room on
   any floor), drawer feeds, and demo triggers.

   Feeds are MJPEG: the browser holds one connection per tile and the server
   pushes frames as fast as the camera produces them. (The old approach — a
   fresh HTTP request per frame — is what made feeds look like they were
   running at a few FPS even when the camera was fine.)
   ========================================================================= */
(() => {
  const strip = document.getElementById('livecamStrip');
  const grid = document.getElementById('camGrid');
  const modal = document.getElementById('camModal');
  const sub = document.getElementById('camModalSub');
  const filtersEl = document.getElementById('camFilters');

  let cams = [], sig = '', zones = [], floors = [], devices = [];
  let floorFilter = 'all';
  const feeds = new Map();          // img element -> stop fn

  fetch('/api/layout').then(r => r.json())
    .then(d => { zones = d.zones.filter(z => !z.is_exit); floors = d.floors || []; buildFilters(); })
    .catch(() => {});
  fetch('/api/cameras/devices').then(r => r.json())
    .then(d => { devices = d.devices || []; }).catch(() => {});

  /* rooms grouped by floor, so a 50-room building stays navigable */
  const zoneOptions = (sel) => floors.map(f => {
    const opts = zones.filter(z => z.floor === f.id).map(z =>
      `<option value="${z.id}" ${z.id === sel ? 'selected' : ''}>${z.name} (${z.id})</option>`).join('');
    return opts ? `<optgroup label="${f.name} — ${f.subtitle}">${opts}</optgroup>` : '';
  }).join('');

  const floorName = (id) => (floors.find(f => f.id === id)?.short) || ('F' + id);

  /* ---- feed manager -------------------------------------------------------
     A browser only allows ~6 simultaneous HTTP/1.1 connections per host, and
     an MJPEG stream holds one open forever. So the tiles the operator is
     actually looking at get a real stream (smooth, full frame rate) and the
     rest fall back to light snapshot polling — never more than a handful of
     connections in total, and the API/websocket never get starved.
  ------------------------------------------------------------------------- */
  const MAX_STREAMS = 5;          // leave a connection free for the REST calls

  function isVisible(img){
    const modalBox = img.closest('.cam-modal');
    if(modalBox) return modalBox.classList.contains('on');
    // a full-screen panel covers everything behind it — those tiles are not
    // being looked at, so they must not hold connections open either
    if(document.querySelector('.cam-modal.on')) return false;
    const drawerBox = img.closest('.drawer');
    if(drawerBox) return drawerBox.classList.contains('on');
    return !!img.offsetParent;                      // strip tile
  }
  /* Lower number = gets a real stream first. A camera that is actually live,
     or one that is seeing fire, is the one you must not watch stutter.

     The strip is the operator's standing view — it is how you watch the whole
     building on a quiet day, not just during an incident — so it outranks the
     thumbnails on the plan. Those stay useful at poll rate: they answer "which
     room is this" at a glance, while the strip is what you actually watch. */
  function priorityOf(img, f){
    const c = cams.find(x => x.id === f.camId) || {};
    let p = img.closest('.drawer') ? 0            // the zone you opened
          : img.closest('.cam-modal') ? 10        // the monitor wall
          : img.closest('.floor-plan') ? 20       // in the room, on the plan
          : 5;                                    // the always-on strip
    if(c.fire || c.smoke) p -= 6;                 // something is happening here
    if(c.status === 'live') p -= 3;               // a real camera, not a demo
    if(c.source_type === 'browser') p -= 4;       // the operator's own camera
    return p;
  }
  /* Ask for the frame at roughly the size it is drawn: a 120 px strip tile does
     not need a 480 px JPEG — that is 4x the bytes, encode and decode for no
     visible difference. */
  function sizeFor(img){
    const w = Math.round((img.getBoundingClientRect().width || 0) *
                         Math.min(window.devicePixelRatio || 1, 2));
    if(!w) return 0;
    return w >= 420 ? 0 : (w <= 200 ? 200 : 320);   // 0 = native 480
  }
  const sizeParams = (img) => {
    const w = sizeFor(img);
    return w ? `&w=${w}&q=${w <= 200 ? 62 : 68}` : '';
  };

  function startFeed(img, camId){
    if(feeds.has(img)) return;
    feeds.set(img, {camId, mode:null, alive:true, timer:null});
    rebalance();
  }
  function stopFeeds(root){
    root.querySelectorAll('img[data-cam-id]').forEach(img => detach(img));
  }
  function detach(img){
    const f = feeds.get(img);
    if(!f) return;
    f.alive = false;
    clearTimeout(f.timer);
    img.onload = img.onerror = null;
    img.removeAttribute('src');
    feeds.delete(img);
  }

  function setMode(img, f, mode){
    if(f.mode === mode) return;
    clearTimeout(f.timer);
    img.onload = img.onerror = null;
    f.mode = mode;
    if(mode === 'stream'){
      const connect = () => { if(f.alive && f.mode==='stream')
        img.src = `/api/camera/${f.camId}/stream?t=${Date.now()}${sizeParams(img)}`; };
      img.onerror = () => { if(f.alive) f.timer = setTimeout(connect, 1200); };
      connect();
    } else if(mode === 'poll'){
      // chained on load: one request in flight at a time, so a slow frame
      // delays the next request instead of piling requests up behind it
      const next = () => { if(f.alive && f.mode==='poll')
        img.src = `/api/camera/${f.camId}/frame?t=${Date.now()}${sizeParams(img)}`; };
      img.onload = () => { f.timer = setTimeout(next, 90); };
      img.onerror = () => { f.timer = setTimeout(next, 900); };
      next();
    } else {
      img.removeAttribute('src');
    }
  }

  function rebalance(){
    [...feeds.keys()].forEach(img => { if(!img.isConnected) detach(img); });
    const entries = [...feeds.entries()];
    const shown = entries.filter(([img]) => isVisible(img))
                         .sort((a,b) => priorityOf(a[0],a[1]) - priorityOf(b[0],b[1]));
    const streaming = new Set(shown.slice(0, MAX_STREAMS).map(([img]) => img));
    entries.forEach(([img, f]) => {
      if(!isVisible(img)) setMode(img, f, 'idle');
      else setMode(img, f, streaming.has(img) ? 'stream' : 'poll');
    });
  }
  setInterval(rebalance, 1200);       // re-evaluate as panels open and close

  const badgeText = (c) => c.status === 'live' ? 'LIVE'
                       : c.status === 'connecting' ? 'CONNECTING'
                       : c.status === 'waiting' ? 'WAITING'
                       : c.status === 'offline' ? 'OFFLINE' : 'DEMO';
  const badgeCls = (c) => c.status === 'live' ? 'live'
                       : c.status === 'offline' ? 'off'
                       : c.status === 'connecting' || c.status === 'waiting' ? 'wait' : 'demo';

  /* ---- floor filter chips in the Camera Monitor ---- */
  function buildFilters(){
    if(!filtersEl) return;
    filtersEl.innerHTML = `<button class="cam-filter on" data-ff="all">All floors</button>` +
      floors.map(f => `<button class="cam-filter" data-ff="${f.id}">${f.name}</button>`).join('');
    filtersEl.querySelectorAll('.cam-filter').forEach(b => b.addEventListener('click', () => {
      floorFilter = b.dataset.ff;
      filtersEl.querySelectorAll('.cam-filter').forEach(x => x.classList.toggle('on', x === b));
      sig = ''; // force a rebuild
    }));
  }

  const visible = () => cams.filter(c =>
    floorFilter === 'all' || String(c.floor) === String(floorFilter));

  /* ---- build tiles when the camera set changes ---- */
  function build(){
    stopFeeds(strip); stopFeeds(grid);

    strip.innerHTML = cams.map(c => `
      <div class="livecam-mini${c.source_type==='browser'?' mine':''}" data-cam="${c.id}"
           title="${c.name} · ${c.zone_name||c.zone_id}">
        <img data-cam-id="${c.id}" alt="${c.name}">
        ${c.source_type==='browser'?'<div class="lm-mine">📷 YOUR CAM</div>':''}
        <div class="lm-top"><span class="lm-zone">${floorName(c.floor)} · ${c.zone_id}</span>
          <span class="lm-fps" data-fps>–</span></div>
        <div class="lm-bot"><span class="lm-tag" data-fire>—</span><span class="lm-tag ppl" data-ppl>0</span></div>
      </div>`).join('');
    strip.querySelectorAll('img').forEach(img => startFeed(img, img.dataset.camId));
    strip.querySelectorAll('.livecam-mini').forEach(t => t.addEventListener('click', openModal));

    const devOpts = devices.length
      ? `<div class="cam-devices">Detected on this machine: ` +
        devices.map(d => `<button class="cam-dev" data-dev="${d.index}">${d.label} · ${d.resolution}</button>`).join('') +
        `</div>` : '';

    grid.innerHTML = visible().map(c => `
      <div class="cam-tile" data-cam="${c.id}">
        <div class="cam-view">
          <img data-cam-id="${c.id}" alt="${c.name}">
          <div class="cam-badge ${badgeCls(c)}" data-badge><span class="rec"></span>${badgeText(c)}</div>
          <div class="cam-fps" data-fps>– FPS</div>
          <div class="cam-scanline"></div>
        </div>
        <div class="cam-info">
          <div class="cam-title">
            <div><div class="cam-name">${c.name}</div>
              <div class="cam-zone">${c.calibrated?'<span class="cam-cal-on" title="Mapped to the floor plan">⊹</span> ':''}${floorName(c.floor)} · ${c.zone_name || c.zone_id} · ${
                c.source_type==='browser' ? 'your device camera'
                : c.source_type==='usb' ? 'USB camera '+c.source
                : c.source_type==='synthetic' ? 'demo feed' : c.source_type}</div></div>
            <div style="display:flex;gap:6px">
              <button class="btn" data-cfg="${c.id}" style="padding:6px 9px;font-size:11px" title="Configure: source, room, remove">⚙</button>
              <button class="btn${c.calibrated?' on':''}" data-cal-cam="${c.id}"
                style="padding:6px 9px;font-size:11px"
                title="${c.calibrated?'Recalibrate this camera to the floor plan'
                                     :'Map this camera to the floor plan'}">⊹</button>
              <button class="btn" data-trigger="${c.id}" style="padding:6px 11px;font-size:11px">⚡ Fire</button>
              <button class="btn" data-smoke="${c.id}" style="padding:6px 9px;font-size:11px"
                title="Simulate this camera seeing smoke without flame">💨</button>
              ${c.obstruction !== undefined && c.obstruction !== null || c.watches_egress
                ? `<button class="btn" data-block="${c.id}" style="padding:6px 9px;font-size:11px"
                     title="Simulate a blocked escape route">⛔</button>` : ''}
              ${(c.doors||[]).length
                ? `<button class="btn" data-door="${c.id}" style="padding:6px 9px;font-size:11px"
                     title="Simulate this fire door being propped open">🚪</button>` : ''}
              ${(c.assets||[]).length
                ? `<button class="btn" data-asset="${c.id}" style="padding:6px 9px;font-size:11px"
                     title="Simulate this extinguisher being blocked, then missing">🧯</button>` : ''}
            </div>
          </div>
          ${c.open_error ? `<div class="cam-err">⚠ ${c.open_error}</div>` : ''}
          <div class="cam-egress" data-egress="${c.id}"></div>
          <div class="cam-doors" data-doors="${c.id}"></div>
          <div class="cam-cfg" data-cfgbox="${c.id}">
            <label>Camera source</label>
            <input data-src placeholder="0 = USB webcam · http://ip:8080/video · rtsp://…" value="${c.source||''}">
            ${devOpts}
            <label>Assigned room</label>
            <select data-zone>${zoneOptions(c.zone_id)}</select>
            <div class="cam-cfg-row">
              <button class="btn" data-apply="${c.id}">Apply</button>
              <span class="cam-cfg-hint">USB index, IP-Webcam URL, RTSP, video file, or "synthetic"</span>
            </div>
            <div class="cam-cfg-danger">
              <button class="btn btn-danger" data-del="${c.id}">🗑 Remove this camera</button>
              <span class="cam-cfg-hint">Takes the camera off the plant. The room keeps its sensors.</span>
            </div>
          </div>
          <div class="cam-stats">
            <div class="cam-stat fire" data-fire><div class="cs-val">–</div><div class="cs-lab">Fire</div></div>
            <div class="cam-stat people" data-ppl><div class="cs-val">0</div><div class="cs-lab">People</div></div>
            <div class="cam-stat smoke" data-smoke><div class="cs-val">–</div><div class="cs-lab">Smoke</div></div>
          </div>
        </div>
      </div>`).join('') + `
      <div class="cam-tile cam-add" id="camAddTile">
        <div class="cam-add-inner">
          <div class="cam-add-plus">＋</div>
          <div class="cam-add-lab">Add camera / room</div>
          <div class="cam-add-form" id="camAddForm">
            <input id="addName" placeholder="Camera name (e.g. Canteen Cam)">
            <select id="addZone">${zoneOptions('')}</select>
            <input id="addSrc" placeholder="Source: 0 / http://… / rtsp://… (blank = demo)">
            ${devOpts}
            <button class="btn" id="addApply">Add camera</button>
          </div>
        </div>
      </div>`;

    grid.querySelectorAll('img').forEach(img => startFeed(img, img.dataset.camId));
    grid.querySelectorAll('[data-trigger]').forEach(b => b.addEventListener('click', () => triggerCam(b.dataset.trigger)));
    grid.querySelectorAll('[data-smoke]').forEach(b => b.addEventListener('click', () => smokeCam(b.dataset.smoke)));
    grid.querySelectorAll('[data-cal-cam]').forEach(b => b.addEventListener('click', () => {
      const c = cams.find(x => x.id === b.dataset.calCam);
      if(c && window.SAFEX_CALIBRATE) window.SAFEX_CALIBRATE(c);
    }));
    grid.querySelectorAll('[data-asset]').forEach(b => b.addEventListener('click', async () => {
      // cycles clear -> access blocked -> missing -> clear, so one button walks
      // a judge through both asset failures
      const c = cams.find(x => x.id === b.dataset.asset) || {};
      const st = ((c.assets||[])[0]||{}).state;
      const next = st === 'BLOCKED' ? 'asset_missing'
                 : st === 'MISSING' ? 'asset_ok' : 'asset_blocked';
      await SAFEX.post(`/api/camera/${b.dataset.asset}/trigger`, {kind: next});
      SAFEX.toast({asset_blocked:'🧯 Extinguisher access blocked',
                   asset_missing:'🧯 Extinguisher missing from its bracket',
                   asset_ok:'Extinguisher restored'}[next], '🧯');
    }));
    grid.querySelectorAll('[data-door]').forEach(b => b.addEventListener('click', async () => {
      const c = cams.find(x => x.id === b.dataset.door) || {};
      const open = (c.doors||[]).some(d => d.violation);
      await SAFEX.post(`/api/camera/${b.dataset.door}/trigger`,
                       {kind: open ? 'door_closed' : 'door_open'});
      SAFEX.toast(open ? 'Fire door released' : '🚪 Fire door propped open', '🚪');
    }));
    grid.querySelectorAll('[data-block]').forEach(b => b.addEventListener('click', async () => {
      const c = cams.find(x => x.id === b.dataset.block) || {};
      const on = c.obstruction && c.obstruction.severity !== 'CLEAR';
      await SAFEX.post(`/api/camera/${b.dataset.block}/trigger`,
                       {kind: on ? 'clear_obstruction' : 'block'});
      SAFEX.toast(on ? 'Escape route cleared' : '⛔ Escape route blocked', on ? '○' : '⛔');
    }));
    grid.querySelectorAll('[data-cfg]').forEach(b => b.addEventListener('click', () => {
      grid.querySelector(`[data-cfgbox="${b.dataset.cfg}"]`).classList.toggle('on'); }));
    // deletion is destructive: only ever act on a real user click
    grid.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', e => {
      if(e.isTrusted) delCam(b.dataset.del);
    }));
    grid.querySelectorAll('[data-apply]').forEach(b => b.addEventListener('click', () => applyCfg(b.dataset.apply)));
    // clicking a detected device fills the nearest source input
    grid.querySelectorAll('.cam-dev').forEach(b => b.addEventListener('click', () => {
      const box = b.closest('.cam-cfg, .cam-add-form');
      const input = box?.querySelector('[data-src]') || box?.querySelector('#addSrc');
      if(input) input.value = b.dataset.dev;
    }));
    const addTile = grid.querySelector('#camAddTile');
    addTile.querySelector('.cam-add-plus').addEventListener('click', () => addTile.classList.toggle('on'));
    addTile.querySelector('.cam-add-lab').addEventListener('click', () => addTile.classList.toggle('on'));
    grid.querySelector('#addApply').addEventListener('click', addCam);

    const map = {}; cams.forEach(c => map[c.zone_id] = c.name);
    if(window.SAFEX_FLOOR) window.SAFEX_FLOOR.setCamMap(map);
  }

  /* ---- per-tick stat update ---- */
  function update(state){
    cams = state.cameras || [];
    const newSig = cams.map(c => c.id + c.source_type + c.status + c.zone_id + (c.calibrated?'C':'') + ((c.doors||[]).length) + ((c.assets||[]).length)).join('|') + '#' + floorFilter;
    if(newSig !== sig){ sig = newSig; build(); }
    const modeLive = state.meta.mode === 'live';

    cams.forEach(c => {
      const mini = strip.querySelector(`.livecam-mini[data-cam="${c.id}"]`);
      if(mini){
        mini.classList.toggle('det-fire', c.fire);
        mini.querySelector('[data-fps]').textContent = c.fps;
        const f = mini.querySelector('[data-fire]');
        f.textContent = c.fire ? '🔥 FIRE' : c.smoke ? '💨 SMOKE' : 'CLEAR';
        f.classList.toggle('on', c.fire || c.smoke);
        mini.querySelector('[data-ppl]').textContent = '◍ ' + c.people;
      }
      const tile = grid.querySelector(`.cam-tile[data-cam="${c.id}"]`);
      if(tile){
        tile.classList.toggle('det-fire', c.fire);
        tile.querySelector('[data-fps]').textContent =
          c.detect_fps ? `${c.fps} FPS · ${c.detect_fps} det/s` : `${c.fps} FPS`;
        const bd = tile.querySelector('[data-badge]');
        bd.className = 'cam-badge ' + badgeCls(c);
        bd.innerHTML = `<span class="rec"></span>${badgeText(c)}`;
        const fs = tile.querySelector('[data-fire]'), ss = tile.querySelector('[data-smoke]');
        fs.className = 'cam-stat fire' + (c.fire?'':' no');
        fs.querySelector('.cs-val').textContent = c.fire ? `${Math.round(c.fire_conf*100)}%` : 'NO';
        ss.className = 'cam-stat smoke' + (c.smoke?'':' no');
        ss.querySelector('.cs-val').textContent = c.smoke ? `${Math.round(c.smoke_conf*100)}%` : 'NO';
        tile.querySelector('[data-ppl] .cs-val').textContent = c.people;
        const eg = tile.querySelector('[data-egress]');
        if(eg){
          const o = c.obstruction;
          const bad = o && o.severity && !['CLEAR','PENDING'].includes(o.severity);
          eg.className = 'cam-egress' + (bad ? ' bad ' + o.severity.toLowerCase() : '');
          eg.innerHTML = !o ? ''
            : bad ? `<b>⛔ ${o.severity}</b> ${o.note || ''}<span>${o.summary||''}</span>`
            : `<b>✓ EGRESS CLEAR</b>${o.reference_ready ? '' : '<span>learning this corridor…</span>'}`;
        }
        const dz = tile.querySelector('[data-doors]');
        if(dz){
          dz.innerHTML = (c.doors||[]).map(d => {
            const bad = d.violation, cal = d.state !== 'UNCALIBRATED';
            return `<div class="cam-door ${bad?'bad':''} ${cal?'':'uncal'}">
              <b>${bad?'🚪 '+d.state.replace('_',' '):cal?'🚪 CLOSED':'🚪 NOT CALIBRATED'}</b>
              <span>${d.label||d.door}${d.note?' · '+d.note:''}</span>
              ${cal?'':`<button class="cam-cal" data-cal="${d.door}">Capture closed</button>`}
            </div>`;
          }).join('');
          dz.querySelectorAll('[data-cal]').forEach(b => b.addEventListener('click', async e => {
            e.stopPropagation();
            const r = await SAFEX.post(`/api/door/${b.dataset.cal}/calibrate`, {});
            SAFEX.toast(r.ok ? 'Door learnt in its closed position'
                             : (r.error||'Could not learn this door'), r.ok?'✓':'⚠');
          }));
        }
        const btn = tile.querySelector('[data-trigger]');
        const firing = c.fire || c.smoke;
        btn.textContent = firing ? '○ Clear' : '⚡ Fire';
        btn.dataset.firing = firing ? '1' : '';
        // the demo trigger only makes sense for feeds that aren't really live
        btn.style.display = c.status === 'live' ? 'none' : '';
        const sbtn = tile.querySelector('[data-smoke]');
        if(sbtn){
          // smoke without flame is its own case — it is what a smouldering
          // fire looks like first, and it is the one the scenario console
          // cannot reach in Live mode
          const smoking = c.smoke && !c.fire;
          sbtn.textContent = smoking ? '○' : '💨';
          sbtn.title = smoking ? 'Clear the simulated smoke'
                               : 'Simulate this camera seeing smoke without flame';
          sbtn.classList.toggle('on', !!smoking);
          sbtn.dataset.smoking = smoking ? '1' : '';
          sbtn.style.display = c.status === 'live' ? 'none' : '';
        }
      }
    });
    if(sub) sub.textContent = modeLive
      ? 'LIVE MODE · camera detections are driving the plant · YOLOv8 fire · smoke · person counting'
      : 'SIMULATION MODE · feeds mirror plant state · switch to ◉ LIVE CAM (top bar) to let real detections drive hazards';
  }

  async function applyCfg(id){
    const box = grid.querySelector(`[data-cfgbox="${id}"]`);
    const src = box.querySelector('[data-src]').value.trim();
    const zone = box.querySelector('[data-zone]').value;
    const r = await SAFEX.post('/api/cameras/config', {camera_id:id, source: src || 'synthetic', zone_id: zone});
    if(r.ok){
      SAFEX.toast(`${id} → ${r.zone_name || zone}${src?' · '+src:''}`, '⚙');
      box.classList.remove('on'); sig='';
    } else SAFEX.toast(r.error || 'Config failed', '⚠');
  }
  async function delCam(id){
    const c = cams.find(x => x.id === id) || {};
    if(!confirm(`Remove "${c.name || id}" from ${c.zone_name || c.zone_id || 'the plant'}?

`
              + `The camera stops feeding this room. You can add it again at any time.`)) return;
    const r = await SAFEX.post('/api/cameras/remove', {camera_id:id});
    if(r.ok){ SAFEX.toast(`Camera ${id} deleted`, '🗑'); sig=''; }
    else SAFEX.toast(r.error || 'Delete failed', '⚠');
  }
  async function addCam(){
    const name = document.getElementById('addName').value.trim() || 'New Camera';
    const zone = document.getElementById('addZone').value;
    const src = document.getElementById('addSrc').value.trim() || 'synthetic';
    const r = await SAFEX.post('/api/cameras/add', {name, zone_id: zone, source: src});
    if(r.ok){
      SAFEX.toast(`Camera added → ${r.zone_name || zone}`, '＋');
      if(src !== 'synthetic') SAFEX.toast('Connecting to source…', '◉');
      sig='';
    } else SAFEX.toast(r.error || 'Add failed', '⚠');
  }

  async function smokeCam(id){
    const b = grid.querySelector(`.cam-tile[data-cam="${id}"] [data-smoke]`);
    const smoking = b && b.dataset.smoking === '1';
    if(SAFEX.mode() !== 'live') SAFEX.toast('Tip: switch to ◉ LIVE CAM so detections drive hazards','◉');
    await SAFEX.post(`/api/camera/${id}/trigger`, {kind: smoking ? 'clear' : 'smoke'});
    SAFEX.toast(smoking ? 'Smoke cleared' : `💨 Smoke detected on ${id}`, smoking ? '○' : '💨');
  }

  async function triggerCam(id){
    const t = grid.querySelector(`.cam-tile[data-cam="${id}"] [data-trigger]`);
    const firing = t && t.dataset.firing === '1';
    if(SAFEX.mode() !== 'live') SAFEX.toast('Tip: switch to ◉ LIVE CAM so detections drive hazards','◉');
    await SAFEX.post(`/api/camera/${id}/trigger`, {kind: firing ? 'clear' : 'fire'});
    SAFEX.toast(firing ? 'Detection cleared' : `🔥 Fire detection simulated on ${id}`, firing?'○':'🔥');
  }

  /* ---- drawer feeds ---- */
  function mountFeeds(){
    document.querySelectorAll('.dw-camfeed[data-cam-zone]').forEach(box => {
      const zone = box.dataset.camZone;
      const c = cams.find(x => x.zone_id === zone);
      if(!c){ box.innerHTML = '<div class="empty">No camera on this zone</div>'; return; }
      if(!box.querySelector('img')){
        const img = document.createElement('img'); img.dataset.camId = c.id;
        box.appendChild(img); startFeed(img, c.id);
      }
    });
  }
  /* The floor map puts a live feed inside the room it belongs to. It shares
     this feed manager rather than opening its own connections, because the
     browser's six-connection limit is a whole-page budget — two modules each
     "just opening a few streams" is exactly how a page runs out. */
  window.SAFEX_CAM = {
    mountFeeds,
    attach: (img, camId) => startFeed(img, camId),
    detach: (img) => detach(img),
    cameraFor: (roomId) => cams.find(c => c.zone_id === roomId),
  };

  function openModal(){ modal.classList.add('on'); rebalance(); }
  function closeModal(){ modal.classList.remove('on'); rebalance(); }
  document.getElementById('camMonitorBtn').addEventListener('click', openModal);
  document.getElementById('camExpandBtn').addEventListener('click', openModal);
  document.getElementById('camCloseBtn').addEventListener('click', closeModal);

  SAFEX.on(update);
})();
