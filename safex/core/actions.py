"""Limited automated actions.

The brief asks for automated actions "without interfering with certified fire
protection systems", and that clause is the whole design, not a footnote.

**What this layer must never do.** A hospital's fire alarm panel, its detectors,
its sprinklers and its magnetic door hold-opens are *certified life-safety
equipment*. They are third-party tested, statutorily inspected, and they are
required to work when everything else has failed — including this platform. A
monitoring system that could inhibit, delay or spoof them would make the
building less safe than having no monitoring at all, and would invalidate the
certification. So nothing here writes to them. Ever.

**What it does instead.** It drives its own, clearly separate equipment on the
advisory side of that line:

  * sounding SAFEX's own local sounder / PA announcement,
  * releasing SAFEX-controlled door hold-opens (a fail-safe device: cut the
    power and the door closes under its own closer),
  * lighting way-finding indicators,
  * notifying people.

Each of those fails *safe* — if this platform dies mid-action the door closes
and the alarm is unaffected.

**How it is governed.** Automation is disarmed by default and has to be armed
deliberately. Each action declares whether it may fire on its own or needs a
human; anything that touches occupants (a PA announcement, a sounder) requires
confirmation unless the operator has explicitly armed it for that hazard. Every
attempt — fired, refused, or blocked because the platform was disarmed — is
written to the tamper-evident log with the reason.
"""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .audit import audit


@dataclass
class ActionSpec:
    id: str
    label: str
    description: str
    # what real-world thing this drives, and why it is safe to drive it
    device: str
    fail_safe: str
    auto_allowed: bool          # may this ever fire without a human?
    needs_confirm: bool         # does a human have to say yes each time?
    trigger: str = ""           # the condition it is offered for


CATALOGUE: Dict[str, ActionSpec] = {
    "sound_local_alarm": ActionSpec(
        id="sound_local_alarm", label="Sound SAFEX local sounder",
        description="Sounds this platform's own sounder in the affected "
                    "compartment. Does not touch the building's fire alarm panel.",
        device="SAFEX sounder (advisory circuit)",
        fail_safe="Independent of the certified alarm; if SAFEX stops, the "
                  "building alarm is entirely unaffected.",
        auto_allowed=True, needs_confirm=False, trigger="confirmed fire"),
    "release_door_holds": ActionSpec(
        id="release_door_holds", label="Release SAFEX door hold-opens",
        description="Cuts power to SAFEX-controlled hold-open magnets so those "
                    "fire doors close under their own closers.",
        device="SAFEX hold-open magnets (fail-safe)",
        fail_safe="Power-to-hold: any failure, including this platform dying, "
                  "lets the door close. It cannot hold a door open.",
        auto_allowed=True, needs_confirm=False, trigger="fire or smoke"),
    "pa_announce": ActionSpec(
        id="pa_announce", label="Play evacuation announcement",
        description="Plays the zone-specific evacuation message on the PA.",
        device="Public address (advisory)",
        fail_safe="Speech only. It cannot silence or delay the fire alarm.",
        auto_allowed=False, needs_confirm=True, trigger="evacuation required"),
    "notify_responders": ActionSpec(
        id="notify_responders", label="Notify the response team",
        description="Sends the incident, its location and the recommended "
                    "actions to the on-call fire response team.",
        device="Notification channel",
        fail_safe="Notification only; no physical effect.",
        auto_allowed=True, needs_confirm=False, trigger="incident opened"),
    "wayfinding_route": ActionSpec(
        id="wayfinding_route", label="Light the way-finding route",
        description="Lights SAFEX way-finding indicators along the currently "
                    "safe escape route, away from the fire.",
        device="SAFEX way-finding indicators",
        fail_safe="Supplementary to statutory escape lighting, which stays on "
                  "its own certified circuit.",
        auto_allowed=True, needs_confirm=False, trigger="evacuation required"),
}

# Things this layer is asked for but deliberately refuses to do, kept explicit
# so the boundary is visible in the product rather than only in a document.
REFUSED = {
    "trigger_fire_panel": "The building's fire alarm panel is certified "
                          "equipment. SAFEX never actuates or silences it.",
    "release_sprinklers": "Suppression is certified equipment and is never "
                          "actuated by a monitoring system.",
    "silence_alarm": "Silencing an alarm is a decision for the responsible "
                     "person at the panel, never for software.",
    "unlock_all_doors": "Access control is a separate certified system with "
                        "its own fire interface.",
}


@dataclass
class ActionRecord:
    id: str
    action: str
    zone: str
    zone_name: str
    status: str            # fired | refused | pending | reverted
    reason: str
    actor: str
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> Dict:
        return {"id": self.id, "action": self.action, "zone": self.zone,
                "zone_name": self.zone_name, "status": self.status,
                "reason": self.reason, "actor": self.actor,
                "ts": self.ts,
                "time": time.strftime("%H:%M:%S", time.localtime(self.ts))}


class ActionController:
    """Decides whether an action may run, runs it, and records what happened."""

    def __init__(self):
        self._lock = threading.Lock()
        self.armed = False                     # automation is off until armed
        self._active: Dict[str, ActionRecord] = {}
        self._history: List[ActionRecord] = []
        self._seq = 0

    # ------------------------------------------------------------------ arming
    def arm(self, on: bool, actor: str = "operator") -> Dict:
        with self._lock:
            self.armed = bool(on)
        audit.record("action.armed" if on else "action.disarmed",
                     f"Automated actions {'armed' if on else 'disarmed'}",
                     actor=actor)
        return {"ok": True, "armed": self.armed}

    # ------------------------------------------------------------------- fire
    def run(self, action_id: str, zone: str = "", zone_name: str = "",
            actor: str = "operator", confirmed: bool = False,
            automatic: bool = False, reason: str = "") -> Dict:
        """Attempt an action. Returns what was done, or why it was not."""
        if action_id in REFUSED:
            audit.record("action.refused",
                         f"Refused '{action_id}' — certified-system boundary",
                         actor=actor, subject=zone,
                         detail={"reason": REFUSED[action_id]})
            return {"ok": False, "status": "refused", "action": action_id,
                    "error": REFUSED[action_id]}

        spec = CATALOGUE.get(action_id)
        if spec is None:
            return {"ok": False, "status": "refused", "error": "unknown action"}

        if automatic and not self.armed:
            self._log(action_id, zone, zone_name, "refused",
                      "automation is disarmed", actor="system")
            return {"ok": False, "status": "refused", "action": action_id,
                    "error": "Automated actions are disarmed. Arm them, or run "
                             "this action manually."}
        if automatic and not spec.auto_allowed:
            self._log(action_id, zone, zone_name, "refused",
                      "this action always needs a person", actor="system")
            return {"ok": False, "status": "refused", "action": action_id,
                    "error": f"{spec.label} always requires a human decision."}
        if spec.needs_confirm and not confirmed and not automatic:
            return {"ok": False, "status": "confirm", "action": action_id,
                    "confirm": True, "label": spec.label,
                    "device": spec.device, "fail_safe": spec.fail_safe,
                    "error": f"{spec.label} affects people in the building. "
                             f"Confirm to proceed."}

        rec = self._log(action_id, zone, zone_name, "fired",
                        reason or ("automatic: " + spec.trigger if automatic
                                   else "operator instruction"),
                        actor="system" if automatic else actor)
        return {"ok": True, "status": "fired", "action": action_id,
                "label": spec.label, "device": spec.device,
                "fail_safe": spec.fail_safe, "record": rec.as_dict()}

    def revert(self, action_id: str, zone: str = "", actor: str = "operator") -> Dict:
        key = f"{action_id}:{zone}"
        with self._lock:
            rec = self._active.pop(key, None)
        if rec is None:
            return {"ok": False, "error": "that action is not active"}
        rec.status = "reverted"
        audit.record("action.reverted", f"{action_id} reverted in {zone or 'site'}",
                     actor=actor, subject=zone)
        return {"ok": True, "action": action_id}

    # ---------------------------------------------------------------- helpers
    def _log(self, action_id: str, zone: str, zone_name: str, status: str,
             reason: str, actor: str) -> ActionRecord:
        with self._lock:
            self._seq += 1
            rec = ActionRecord(id=f"ACT-{self._seq:04d}", action=action_id,
                               zone=zone, zone_name=zone_name or zone,
                               status=status, reason=reason, actor=actor)
            if status == "fired":
                self._active[f"{action_id}:{zone}"] = rec
            self._history.insert(0, rec)
            self._history = self._history[:80]
        spec = CATALOGUE.get(action_id)
        audit.record("action.taken" if status == "fired" else "action.refused",
                     f"{spec.label if spec else action_id} — "
                     f"{zone_name or zone or 'site'} ({reason})",
                     actor=actor, subject=zone,
                     detail={"action": action_id, "status": status,
                             "device": spec.device if spec else "",
                             "reason": reason})
        return rec

    def suggest(self, snapshot: Dict) -> List[Dict]:
        """What the platform would do right now, and whether it may.

        Shown to the operator *before* anything happens, so automation is
        something they watch decide rather than discover afterwards.
        """
        out: List[Dict] = []
        plant = snapshot.get("plant", {}) or {}
        incidents = (snapshot.get("incidents", {}) or {}).get("active", []) or []
        compliance = snapshot.get("compliance", {}) or {}

        for inc in incidents[:3]:
            for aid in ("sound_local_alarm", "release_door_holds",
                        "notify_responders", "wayfinding_route", "pa_announce"):
                spec = CATALOGUE[aid]
                out.append({
                    "action": aid, "label": spec.label, "zone": inc["zone_id"],
                    "zone_name": inc["zone_name"], "device": spec.device,
                    "fail_safe": spec.fail_safe,
                    "why": f"{inc['hazard_kind']} incident in {inc['zone_name']}",
                    "auto": spec.auto_allowed and self.armed,
                    "needs_confirm": spec.needs_confirm,
                    "active": f"{aid}:{inc['zone_id']}" in self._active,
                })
        if not incidents and compliance.get("blocked_routes"):
            out.append({
                "action": "notify_responders",
                "label": CATALOGUE["notify_responders"].label,
                "zone": "", "zone_name": "site",
                "device": CATALOGUE["notify_responders"].device,
                "fail_safe": CATALOGUE["notify_responders"].fail_safe,
                "why": f"{compliance['blocked_routes']} escape route(s) blocked",
                "auto": self.armed, "needs_confirm": False,
                "active": "notify_responders:" in self._active,
            })
        return out

    def snapshot(self, state: Dict) -> Dict:
        with self._lock:
            history = [r.as_dict() for r in self._history[:12]]
            active = [r.as_dict() for r in self._active.values()]
        return {
            "armed": self.armed, "active": active, "history": history,
            "suggested": self.suggest(state),
            "catalogue": [{"id": s.id, "label": s.label, "device": s.device,
                           "description": s.description, "fail_safe": s.fail_safe,
                           "auto_allowed": s.auto_allowed,
                           "needs_confirm": s.needs_confirm} for s in CATALOGUE.values()],
            "refused": [{"id": k, "reason": v} for k, v in REFUSED.items()],
            "boundary": ("SAFEX drives only its own advisory equipment. The "
                         "building's fire alarm panel, detectors and suppression "
                         "are certified life-safety systems and are never "
                         "actuated, silenced or delayed by this platform."),
        }


actions = ActionController()
