/* SAFEX AI — AI Safety Copilot chat panel. */
(() => {
  const chat=document.getElementById('chat'), box=document.getElementById('chatBox'),
        send=document.getElementById('chatSend'), suggestEl=document.getElementById('chatSuggest'),
        enginePill=document.getElementById('copilotEngine');
  const SUGGEST=["Overall status?","Most dangerous zone?","What should I do now?","Evacuation plan","Camera detections?"];

  function bubble(text,who){
    const d=document.createElement('div'); d.className='msg '+who;
    d.innerHTML=text.replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>');
    chat.appendChild(d); chat.scrollTop=chat.scrollHeight; return d;
  }
  function typing(){ const d=document.createElement('div'); d.className='msg bot typing';
    d.innerHTML='<span></span><span></span><span></span>'; chat.appendChild(d); chat.scrollTop=chat.scrollHeight; return d; }

  async function ask(q){
    if(!q.trim()) return; bubble(q,'user'); box.value='';
    const t=typing();
    try{ const r=await SAFEX.post('/api/copilot',{question:q}); t.remove(); bubble(r.answer,'bot');
      if(r.engine) enginePill.textContent = r.engine.includes('groq')?'LLaMA-3.3':'grounded';
    }catch(e){ t.remove(); bubble("I couldn't reach the plant intelligence engine. Check the server is running.",'bot'); }
  }
  suggestEl.innerHTML=SUGGEST.map(s=>`<button>${s}</button>`).join('');
  suggestEl.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>ask(b.textContent)));
  send.addEventListener('click',()=>ask(box.value));
  box.addEventListener('keydown',e=>{ if(e.key==='Enter') ask(box.value); });

  let greeted=false;
  SAFEX.on(()=>{ if(greeted) return; greeted=true;
    bubble("👋 I'm your **SAFEX Safety Copilot**, monitoring every zone and camera in real time. Ask me about status, the biggest risk, evacuation, or live camera detections.",'bot'); });
})();
