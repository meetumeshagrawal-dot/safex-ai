"""
AI Safety Copilot.

A conversational safety officer that answers operator questions *grounded in the
live plant state* — never hallucinated. The Copilot always runs a deterministic,
state-aware reasoning engine so it works with zero external dependencies and
never fails mid-demo. If an LLM backend is configured (Groq / LLaMA-3.3 via
`SAFEX_LLM=groq` + `GROQ_API_KEY`), the Copilot hands the same structured
context to the model to author a more fluent narrative, while the deterministic
engine remains the safety net.
"""
from __future__ import annotations
import json
import re
from typing import Callable, Dict, List, Optional

from ..settings import GROQ_API_KEY, GROQ_MODEL, LLM_PROVIDER


class Copilot:
    def __init__(self, state_provider: Callable[[], dict]):
        # state_provider() returns the latest full plant snapshot
        self.state = state_provider

    # ------------------------------------------------------------------ intents
    def answer(self, question: str) -> dict:
        q = question.lower().strip()
        snap = self.state()

        # Route to a grounded handler based on intent keywords.
        if any(w in q for w in ["status", "overall", "how are we", "summary",
                                "situation", "sitrep"]):
            text = self._status(snap)
        elif any(w in q for w in ["most danger", "worst", "highest risk",
                                  "which zone", "priorit"]):
            text = self._worst(snap)
        elif any(w in q for w in ["evacuat", "exit", "route", "escape", "get out"]):
            text = self._evac(snap)
        elif any(w in q for w in ["predict", "forecast", "trend", "about to",
                                  "going to", "escalat"]):
            text = self._predict(snap)
        elif any(w in q for w in ["incident", "what happened", "log", "timeline"]):
            text = self._incidents(snap)
        elif any(w in q for w in ["people", "workers", "occupanc", "how many",
                                  "at risk", "personnel"]):
            text = self._people(snap)
        elif any(w in q for w in ["recommend", "what should", "action", "do now",
                                  "advice", "next step"]):
            text = self._recommend(snap)
        elif any(w in q for w in ["camera", "detect", "cctv", "surveillance", "feed", "cam "]):
            text = self._cameras(snap)
        elif zone := self._match_zone(q, snap):
            text = self._zone_detail(snap, zone)
        elif any(w in q for w in ["hello", "hi ", "hey", "help", "what can you"]):
            text = self._help()
        else:
            text = self._status(snap)

        # Optional LLM polish (kept safe: falls back silently on any error)
        if LLM_PROVIDER == "groq" and GROQ_API_KEY:
            polished = self._llm_polish(question, snap, text)
            if polished:
                return {"answer": polished, "grounded_facts": text,
                        "engine": "groq-llama"}
        return {"answer": text, "engine": "local-grounded"}

    # ---------------------------------------------------------------- handlers
    def _status(self, s) -> str:
        p = s["plant"]
        n_inc = s["incidents"]["active_count"]
        risk = s["evacuation"]["people_at_risk"]
        emoji = {"NORMAL": "🟢", "ELEVATED": "🟡", "HIGH": "🟠",
                 "CRITICAL": "🔴"}.get(p["band"], "⚪")
        lines = [f"{emoji} **Plant status: {p['band']}** — hazard index "
                 f"{p['score']}/100."]
        if n_inc:
            top = s["incidents"]["active"][0]
            lines.append(f"There {'is' if n_inc==1 else 'are'} **{n_inc} active "
                         f"incident{'s' if n_inc>1 else ''}**, the most severe "
                         f"being {top['severity']} in {top['zone_name']}.")
        else:
            lines.append("No active incidents — all zones within safe envelope.")
        if risk:
            lines.append(f"**{risk} worker(s)** are currently inside "
                         f"elevated-risk zones and have live evacuation routes "
                         f"assigned.")
        return " ".join(lines)

    def _worst(self, s) -> str:
        zones = sorted(s["zones"], key=lambda z: -z["score"])
        if not zones:
            return "No zone telemetry available yet."
        top = zones[0]
        if top["score"] < 30:
            return (f"Nothing critical right now. The highest-risk zone is "
                    f"**{top['name']}** at just {top['score']}/100 — well within "
                    f"safe limits.")
        drivers = ", ".join(top["drivers"]) or "elevated sensor readings"
        rest = ", ".join(f"{z['name']} ({z['score']})" for z in zones[1:3])
        return (f"🎯 The top priority is **{top['name']}** at **{top['score']}/100 "
                f"({top['band']})**, driven by {drivers}. "
                f"Next watch zones: {rest}.")

    def _evac(self, s) -> str:
        ev = s["evacuation"]
        if ev["people_at_risk"] == 0:
            return ("No evacuation required — no workers are in elevated-risk "
                    "zones. All muster points are clear.")
        rec = ev.get("recommended_exit")
        load = ev["exit_load"]
        load_str = ", ".join(f"{k}: {v}" for k, v in load.items() if v)
        blocked = ev.get("blocked_zones", [])
        out = [f"🧭 **{ev['people_at_risk']} worker(s)** are being routed to "
               f"muster points. Recommended primary egress: **{rec}**."]
        if load_str:
            out.append(f"Projected exit load — {load_str}.")
        if blocked:
            out.append(f"⚠️ Routes from {', '.join(blocked)} are **blocked** by "
                       f"hazard — open alternative egress now.")
        return " ".join(out)

    def _predict(self, s) -> str:
        wl = s.get("watchlist", [])
        if not wl:
            return ("📈 Predictive engine sees no zones trending into danger. "
                    "Risk gradients are flat or falling across the plant.")
        parts = []
        for w in wl[:3]:
            eta = (f", crossing the alarm line in ~{w['eta_ticks']}s"
                   if w.get("eta_ticks") else "")
            parts.append(f"**{w['zone_name']}** ({w['current']}→{w['predicted']}"
                         f", conf {int(w['confidence']*100)}%{eta})")
        return ("📈 Predictive watchlist — zones on a rising trajectory: "
                + "; ".join(parts) + ". Recommend pre-emptive inspection.")

    def _incidents(self, s) -> str:
        inc = s["incidents"]
        if not inc["active"]:
            if inc["history"]:
                last = inc["history"][0]
                return (f"No active incidents. The most recent was {last['id']} "
                        f"in {last['zone_name']} ({last['severity']}), resolved "
                        f"after {last['duration_s']}s.")
            return "No incidents logged this session. Clean sheet. ✅"
        lines = [f"There are **{inc['active_count']} active incident(s)**:"]
        for i in inc["active"][:4]:
            lines.append(f"• {i['id']} — {i['severity']} {i['hazard_kind']} in "
                         f"{i['zone_name']} (peak {i['peak_score']}, "
                         f"{i['status']})")
        return "\n".join(lines)

    def _people(self, s) -> str:
        risk = s["evacuation"]["people_at_risk"]
        total = s["kpis"]["workers_protected"] + risk
        if risk == 0:
            return (f"👥 **{total} workers** are on site, all in safe zones. "
                    f"Nobody is currently at risk.")
        zones = [z for z in s["zones"] if z["occupancy"] > 0 and z["score"] > 40]
        zones.sort(key=lambda z: -z["score"])
        detail = ", ".join(f"{z['occupancy']} in {z['name']}" for z in zones[:4])
        return (f"👥 **{risk} of {total} workers** are inside elevated-risk zones: "
                f"{detail}. All have active evacuation routing.")

    def _recommend(self, s) -> str:
        p = s["plant"]
        recs: List[str] = []
        crit = [z for z in s["zones"] if z["band"] == "CRITICAL"]
        high = [z for z in s["zones"] if z["band"] == "HIGH"]
        if crit:
            z = max(crit, key=lambda x: x["score"])
            recs.append(f"1. **Evacuate {z['name']} immediately** and dispatch "
                        f"emergency response — hazard {z['score']}/100.")
        if high:
            z = max(high, key=lambda x: x["score"])
            recs.append(f"{len(recs)+1}. Pre-position a response team at "
                        f"**{z['name']}** ({z['score']}/100) before it escalates.")
        for w in s.get("watchlist", [])[:1]:
            recs.append(f"{len(recs)+1}. Inspect **{w['zone_name']}** — predicted "
                        f"to reach {w['predicted']}/100 shortly.")
        if s["evacuation"]["blocked_zones"]:
            recs.append(f"{len(recs)+1}. Open alternative egress — routes from "
                        f"{', '.join(s['evacuation']['blocked_zones'])} are blocked.")
        if not recs:
            return ("✅ No action required. All zones are within their safe "
                    "operating envelope. Continue routine monitoring.")
        return "Recommended actions, in priority order:\n" + "\n".join(recs)

    def _cameras(self, s) -> str:
        cams = s.get("cameras", [])
        mode = s.get("meta", {}).get("mode", "simulation")
        if not cams:
            return "No cameras are registered on the plant network."
        active = [c for c in cams if c.get("fire") or c.get("smoke")]
        lines = [f"📹 **{len(cams)} cameras online** · mode: "
                 f"{'LIVE — detections driving the plant' if mode=='live' else 'SIMULATION — feeds mirror plant state'}."]
        for c in cams:
            flags = []
            if c.get("fire"): flags.append(f"🔥 FIRE {int(c['fire_conf']*100)}%")
            if c.get("smoke"): flags.append(f"💨 smoke {int(c['smoke_conf']*100)}%")
            flags.append(f"{c['people']} ppl")
            status = "  ".join(flags)
            lines.append(f"• **{c['name']}** ({c['zone_id']}): {status} · {c['fps']} fps"
                         f"{' · LIVE' if c['live'] else ''}")
        if active:
            worst = max(active, key=lambda c: c.get("fire_conf", 0))
            zone = next((z for z in s["zones"] if z["zone_id"] == worst["zone_id"]), None)
            route = s["evacuation"]["routes"].get(worst["zone_id"], {})
            exits = 1 if route and not route.get("blocked") else 0
            if zone:
                lines.append(
                    f"\n⚠️ **{worst['name']}**: fire detected on camera, hazard "
                    f"{zone['score']}/100. {zone['occupancy']} worker(s) remain, "
                    f"{exits} safe exit route mapped. Recommend immediate evacuation.")
        return "\n".join(lines)

    def _zone_detail(self, s, zone) -> str:
        drivers = ", ".join(zone["drivers"]) or "no significant drivers"
        route = s["evacuation"]["routes"].get(zone["zone_id"])
        route_str = ""
        if route and not route.get("blocked"):
            route_str = (f" Evacuation route: {' → '.join(route['path'])} "
                         f"(to {route['exit']}).")
        return (f"**{zone['name']}** — hazard **{zone['score']}/100** "
                f"({zone['band']}), {zone['occupancy']} worker(s) present. "
                f"Key drivers: {drivers}.{route_str}")

    def _help(self) -> str:
        return ("👋 I'm the SAFEX Safety Copilot. I monitor every zone in real "
                "time. Ask me things like: *“What's the overall status?”*, "
                "*“Which zone is most dangerous?”*, *“What should I do now?”*, "
                "*“Show the evacuation plan”*, *“What's about to escalate?”* or "
                "name any zone (e.g. *“Boiler Room”*).")

    # ------------------------------------------------------------------ helpers
    def _match_zone(self, q: str, s) -> Optional[dict]:
        for z in s["zones"]:
            name = z["name"].lower()
            if name in q or z["zone_id"].lower() in q:
                return z
            # partial word match (e.g. "boiler")
            for word in name.split():
                if len(word) > 3 and word in q:
                    return z
        return None

    def _llm_polish(self, question: str, snap: dict, grounded: str) -> Optional[str]:
        """Optionally rewrite the grounded answer via Groq/LLaMA. Safe-fails."""
        try:
            import urllib.request
            ctx = {
                "plant": snap["plant"],
                "top_zones": sorted(snap["zones"], key=lambda z: -z["score"])[:5],
                "incidents": snap["incidents"]["active"][:3],
                "evacuation": {k: snap["evacuation"][k] for k in
                               ("people_at_risk", "recommended_exit",
                                "blocked_zones")},
                "grounded_answer": grounded,
            }
            sys = ("You are the SAFEX AI Safety Copilot for an industrial plant "
                   "control room. Answer ONLY from the provided JSON state and the "
                   "grounded_answer. Be concise, calm, authoritative. Never invent "
                   "numbers. Keep to 3 sentences.")
            payload = {
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": sys},
                    {"role": "user", "content":
                     f"STATE:\n{json.dumps(ctx)}\n\nOPERATOR: {question}"},
                ],
                "temperature": 0.3, "max_tokens": 220,
            }
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode())
            return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return None
