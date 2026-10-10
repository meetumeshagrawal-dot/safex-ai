/* =========================================================================
   SAFEX AI — Browser Webcam streaming.
   Captures the visitor's webcam via getUserMedia, sends JPEG frames to the
   FastAPI backend over a WebSocket (/ws/camera/<id>), receives YOLO detections
   and draws them on a canvas over the live video. This is what makes the
   camera work on a remote (Render) deployment where the server has no camera.
   Request/response pacing self-throttles to ~10 FPS and prevents backlog.
   ========================================================================= */
(() => {
  const openBtn  = document.getElementById('webcamOpenBtn');
  const modal    = document.getElementById('webcamModal');
  const closeBtn = document.getElementById('webcamCloseBtn');
  const zoneSel  = document.getElementById('webcamZone');
  const startBtn = document.getElementById('webcamStart');
  const stopBtn  = document.getElementById('webcamStop');
  const statEl   = document.getElementById('webcamStat');
  const video    = document.getElementById('webcamVideo');
  const overlay  = document.getElementById('webcamOverlay');
  const badge    = document.getElementById('webcamBadge');
  const fpsEl    = document.getElementById('webcamFps');
  const octx     = overlay.getContext('2d');

  const SEND_W = 480, SEND_H = 360;      // downscale before sending (bandwidth)
  const JPEG_Q = 0.7;                    // ~70% quality
  const MIN_INTERVAL = 55;               // ms → up to ~18 FPS; the real pace is
                                         // set by how fast the server answers

  let stream = null, ws = null, streaming = false, wasFire = false;
  let awaiting = false, lastSent = 0, camId = 'CAM-WEB';
  let pumpTimer = null, stallTimer = null;
  const capCanvas = document.createElement('canvas');
  capCanvas.width = SEND_W; capCanvas.height = SEND_H;
  const cctx = capCanvas.getContext('2d');
  let lastBoxes = [], lastW = SEND_W, lastH = SEND_H;

  /* populate the room dropdown from the plant layout, grouped by floor */
  fetch('/api/layout').then(r => r.json()).then(d => {
    const floors = d.floors || [], zones = d.zones.filter(z => !z.is_exit);
    zoneSel.innerHTML = floors.map(f => {
      const opts = zones.filter(z => z.floor === f.id)
        .map(z => `<option value="${z.id}">${z.name} (${z.id})</option>`).join('');
      return opts ? `<optgroup label="${f.name} — ${f.subtitle}">${opts}</optgroup>` : '';
    }).join('');
    const warehouse = [...zoneSel.options].find(o => o.value === 'Z7');
    if (warehouse) zoneSel.value = 'Z7';
  }).catch(() => {});

  function open(){ modal.classList.add('on'); sizeOverlay(); }
  function close(){
    // keep streaming in the background so detections keep driving the zone —
    // only the explicit Stop button (or closing the tab) ends the camera
    modal.classList.remove('on');
  }
  openBtn.addEventListener('click', open);
  closeBtn.addEventListener('click', close);

  function reflectBtn(){
    openBtn.innerHTML = streaming
      ? '📷 Webcam <span style="color:var(--normal)">● LIVE</span>'
      : '📷 Use My Webcam';
  }

  /* ---- start / stop ---- */
  async function startCamera(){
    // secure-context guard (getUserMedia needs https or localhost)
    if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
      statEl.textContent = '⚠ getUserMedia unavailable. Use HTTPS (Render) or localhost, on Chrome/Edge/Firefox.';
      return;
    }
    try{
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'environment' },
        audio: false
      });
    }catch(err){
      statEl.textContent = '⚠ Camera permission denied or no camera: ' + err.message;
      return;
    }
    video.srcObject = stream;
    await video.play().catch(()=>{});

    // register this browser camera onto the chosen room
    const zone = zoneSel.value;
    let reg;
    try{
      const res = await fetch('/api/cameras/browser', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ zone_id: zone, camera_id: 'CAM-WEB', name: 'My Camera' })
      });
      if(!res.ok){
        statEl.innerHTML = `⚠ Server refused the pairing (HTTP ${res.status}).`;
        return;
      }
      reg = await res.json();
      if(!reg.ok){ statEl.textContent = '⚠ ' + (reg.error||'could not register camera'); return; }
      camId = reg.camera_id;
    }catch(e){
      // almost always "the server isn't running" — say so instead of a shrug
      statEl.innerHTML = `⚠ Can't reach the SAFEX server at <b>${location.host}</b>.<br>`
        + `<span style="color:var(--mut2)">Is it still running? Start it with <code>python run.py</code> and try again.</span>`;
      return;
    }

    // open the streaming websocket
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/camera/${camId}`);
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      streaming = true;
      startBtn.style.display = 'none'; stopBtn.style.display = '';
      badge.style.display = ''; sizeOverlay(); reflectBtn();
      const room = reg.zone_name || zone;
      statEl.innerHTML = `● Paired to <b>${room}</b> — your camera is now one of the plant's cameras.<br>`
        + `<span style="color:var(--mut2)">It appears in Live Camera Feeds and the Camera Monitor. `
        + `Close this panel and it keeps running in the background.</span>`;
      SAFEX.toast(`📷 Your camera is live in ${room}`, '📷');
      // put the operator on the floor this camera is watching
      if(window.SAFEX_FLOOR) window.SAFEX_FLOOR.show(zone);
      clearTimeout(pumpTimer); awaiting = false; pump();
    };
    ws.onmessage = (ev) => {
      awaiting = false; clearTimeout(stallTimer);
      let msg; try{ msg = JSON.parse(ev.data); }catch(_){ return; }
      if(msg.ok === false){ statEl.textContent = '⚠ ' + (msg.error||'server error'); return; }
      lastBoxes = msg.boxes || []; lastW = msg.w || SEND_W; lastH = msg.h || SEND_H;
      if(fpsEl) fpsEl.textContent = msg.fps ?? '–';
      drawOverlay(msg);
      const flags = [];
      if(msg.fire) flags.push('🔥 FIRE ' + Math.round((msg.fire_conf||0)*100) + '%');
      if(msg.smoke) flags.push('💨 smoke');
      flags.push((msg.people||0) + ' people');
      if(msg.detect_fps) flags.push(msg.detect_fps + ' detections/s');
      if(!msg.detector) flags.push('⚠ ' + (msg.detector_error || 'YOLO not available on server'));
      statEl.textContent = `● Live · ${flags.join('  ·  ')}`;
      // first frame a fire lands on, take the operator to that room's floor
      if(msg.fire && !wasFire && window.SAFEX_FLOOR) window.SAFEX_FLOOR.show(zoneSel.value);
      wasFire = !!msg.fire;
    };
    ws.onclose = () => { if(streaming) statEl.textContent = 'Connection closed.'; streaming = false; };
    ws.onerror = () => { statEl.textContent = '⚠ websocket error'; };
  }

  function stopCamera(){
    streaming = false;
    clearTimeout(pumpTimer); clearTimeout(stallTimer); awaiting = false;
    try{ ws && ws.close(); }catch(_){}
    ws = null;
    if(stream){ stream.getTracks().forEach(t => t.stop()); stream = null; }
    video.srcObject = null;
    octx.clearRect(0,0,overlay.width,overlay.height);
    startBtn.style.display = ''; stopBtn.style.display = 'none'; badge.style.display = 'none';
    // release the plant zone (clear detection) by pointing the cam idle
    statEl.textContent = 'Stopped.';
    reflectBtn();
  }
  startBtn.addEventListener('click', startCamera);
  stopBtn.addEventListener('click', stopCamera);

  /* ---- capture loop ----------------------------------------------------
     Timer-driven, not requestAnimationFrame: rAF stops completely in a
     background tab, which silently stopped the stream and left the camera
     tile sitting on WAITING until you came back to the tab.

     Only one frame is ever in flight. The server now answers immediately
     (detection runs on its own thread and skips to the newest frame), so this
     keeps pace with the camera instead of the model — no queue builds up and
     the picture cannot drift behind.
  ---------------------------------------------------------------------- */
  function pump(){
    if(!streaming) return;
    pumpTimer = setTimeout(pump, MIN_INTERVAL);
    if(awaiting) return;                       // never queue frames up
    if(!video.videoWidth) return;
    const now = performance.now();
    if(now - lastSent < MIN_INTERVAL) return;
    lastSent = now;
    cctx.drawImage(video, 0, 0, SEND_W, SEND_H);
    capCanvas.toBlob(blob => {
      if(blob && ws && ws.readyState === 1){
        awaiting = true;
        // if a reply is lost, don't wedge the pump forever
        clearTimeout(stallTimer);
        stallTimer = setTimeout(() => { awaiting = false; }, 2000);
        blob.arrayBuffer().then(buf => { try{ ws.send(buf); }catch(_){ awaiting = false; } });
      }
    }, 'image/jpeg', JPEG_Q);
  }

  /* ---- overlay drawing (scale backend 480x360 coords to display size) ---- */
  function sizeOverlay(){
    const r = video.getBoundingClientRect();
    overlay.width = r.width; overlay.height = r.height;
  }
  window.addEventListener('resize', sizeOverlay);
  video.addEventListener('loadedmetadata', sizeOverlay);

  const COL = { fire:'#ff4368', smoke:'#9aa7c7', person:'#4d8dff' };
  function drawOverlay(msg){
    sizeOverlay();
    const sx = overlay.width / lastW, sy = overlay.height / lastH;
    octx.clearRect(0,0,overlay.width,overlay.height);
    octx.lineWidth = 2; octx.font = '13px JetBrains Mono, monospace';
    (lastBoxes||[]).forEach(b => {
      const c = COL[b.label] || '#37ecdb';
      const x = b.x*sx, y = b.y*sy, w = b.w*sx, h = b.h*sy;
      octx.strokeStyle = c; octx.strokeRect(x, y, w, h);
      const tag = `${b.label} ${Math.round((b.conf||0)*100)}%`;
      const tw = octx.measureText(tag).width + 8;
      octx.fillStyle = c; octx.fillRect(x, y-18, tw, 18);
      octx.fillStyle = '#08101c'; octx.fillText(tag, x+4, y-5);
    });
    if(msg.fire){
      octx.strokeStyle = '#ff4368'; octx.lineWidth = 4;
      octx.strokeRect(2,2,overlay.width-4,overlay.height-4);
    }
  }
})();
