/* =========================================================================
   SAFEX AI — core client. Real-time state fabric, cinematic boot, shared
   helpers. Render modules register via SAFEX.on(fn) and read SAFEX.state.
   ========================================================================= */
const SAFEX = (() => {
  const hooks = [];
  let state = null, ws = null, firstFrame = true;

  const BAND_COLOR = {NORMAL:'#25e08a', ELEVATED:'#ffcb52', HIGH:'#ff8f3f', CRITICAL:'#ff4368',
                      NOMINAL:'#25e08a'};
  const BAND_INK   = {NORMAL:'#04331f', ELEVATED:'#332800', HIGH:'#331500', CRITICAL:'#2a0410',
                      NOMINAL:'#04331f'};

  function on(fn){ hooks.push(fn); }
  function emit(){ hooks.forEach(h => { try{ h(state); }catch(e){ console.error(e); } }); }

  /* ---- cinematic boot ---- */
  const BOOT = [
    ['Initialising sensor mesh',1], ['Linking zone telemetry',1],
    ['Calibrating hazard engine',1], ['Loading evacuation graph',1],
    ['Mounting camera subsystem',1], ['Arming AI Safety Copilot',1],
    ['ALL SYSTEMS NOMINAL',0]];
  function runBoot(){
    const el = document.getElementById('bootSteps'), bar = document.getElementById('bootBar');
    let i = 0;
    const iv = setInterval(() => {
      if(i < BOOT.length){
        const [txt, run] = BOOT[i];
        el.innerHTML = run ? `› ${txt} … <span class="ok">OK</span>`
                           : `<span class="ok">▮ ${txt}</span>`;
        if(bar) bar.style.width = ((i+1)/BOOT.length*100) + '%';
        i++;
      } else clearInterval(iv);
    }, 240);
  }
  function dismissBoot(){
    const b = document.getElementById('boot');
    if(b && !b.classList.contains('hidden')) setTimeout(()=>b.classList.add('hidden'), 500);
  }

  /* ---- websocket + fallback ---- */
  function connect(){
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    try{ ws = new WebSocket(`${proto}://${location.host}/ws`); }
    catch(e){ return pollFallback(); }
    ws.onopen = () => setConn(true);
    ws.onmessage = ev => {
      try{ state = JSON.parse(ev.data);
        if(firstFrame){ firstFrame = false; dismissBoot(); }
        emit();
      }catch(e){ console.error('parse', e); }
    };
    ws.onclose = () => { setConn(false); setTimeout(connect, 1500); };
    ws.onerror = () => { try{ ws.close(); }catch(_){}};
  }
  let pollTimer = null;
  function pollFallback(){
    if(pollTimer) return;
    pollTimer = setInterval(async () => {
      try{ const r = await fetch('/api/state'); state = await r.json();
        if(firstFrame){ firstFrame = false; dismissBoot(); }
        setConn(true); emit();
      }catch(e){ setConn(false); }
    }, 1000);
  }
  function setConn(live){
    const c = document.getElementById('conn'), t = document.getElementById('connText');
    if(!c) return;
    c.classList.toggle('live', live); c.classList.toggle('dead', !live);
    t.textContent = live ? 'LIVE · 1HZ' : 'RECONNECT';
  }

  /* ---- REST ---- */
  async function post(path, body){
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body||{})});
    return r.json();
  }

  /* ---- clock ---- */
  function startClock(){
    const el = document.getElementById('clock');
    setInterval(()=>{ el.textContent = new Date().toLocaleTimeString('en-GB'); }, 1000);
  }

  /* ---- toast ---- */
  function toast(msg, ico='⬢'){
    let t = document.getElementById('toast');
    if(!t){ t = document.createElement('div'); t.id='toast'; document.body.appendChild(t); }
    t.innerHTML = `<span class="t-ico">${ico}</span><span>${msg}</span>`;
    t.classList.add('on');
    clearTimeout(t._h); t._h = setTimeout(()=>t.classList.remove('on'), 2600);
  }

  /* ---- number tween ---- */
  const _tweens = new WeakMap();
  /* Is the browser actually running animation frames? A hidden or
     non-compositing tab suspends them, and every counter on the dashboard is
     animated — so without this check the numbers silently freeze at whatever
     they were when the operator switched away, which is worse than not
     animating at all. Re-probed continuously, so it recovers on return. */
  let _rafAlive = false;
  const _probeRaf = () => { _rafAlive = false; requestAnimationFrame(() => { _rafAlive = true; }); };
  _probeRaf();
  setInterval(_probeRaf, 2000);

  function tween(el, to, decimals=1){
    if(!_rafAlive){                       // no frames: just tell the truth now
      _tweens.set(el, to);
      el.textContent = to.toFixed(decimals);
      return;
    }
    const from = _tweens.get(el) ?? to; _tweens.set(el, to);
    const start = performance.now(), dur = 500;
    function step(now){
      const p = Math.min(1,(now-start)/dur), e = 1-Math.pow(1-p,3);
      el.textContent = (from + (to-from)*e).toFixed(decimals);
      if(p<1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }


  function mode(){ return state?.meta?.mode || 'simulation'; }

  function boot(){ runBoot(); startClock(); connect(); setTimeout(dismissBoot, 7000); }

  return {on, boot, post, toast, tween, mode,
          get state(){return state;}, BAND_COLOR, BAND_INK};
})();
document.addEventListener('DOMContentLoaded', SAFEX.boot);
