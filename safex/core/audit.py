"""Tamper-evident audit log.

An inspector's question is never "what is happening now" — it is "what happened
on the night of the 14th, and can you prove this record has not been edited
since?" That is a different job from the live dashboard, and it needs different
properties: append-only, timestamped, ordered, and verifiable.

Every record carries the hash of the record before it, so each entry commits to
the entire history that precedes it. Change one field in an old line and every
hash after it stops matching, and `verify()` reports exactly where the chain
first breaks.

**Be precise about what that does and does not give you.** This makes the log
*tamper-evident*, not tamper-proof: somebody with write access to the file and
this source code could rewrite every record and recompute the whole chain. What
stops that in a real deployment is anchoring — periodically publishing the head
hash somewhere the operator does not control (emailed to the fire officer,
written to append-only storage, or committed to a ticket). The log exposes its
head hash for exactly that purpose. Claiming more than this would be worse than
useless in an audit, because it would be believed.
"""
from __future__ import annotations
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional

ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "data" / "audit"
GENESIS = "0" * 64

# Event kinds, grouped so a report can ask for "just the compliance findings".
KINDS = {
    "incident.opened", "incident.resolved", "incident.acknowledged",
    "compliance.violation.opened", "compliance.violation.closed",
    "detection.fire", "detection.smoke",
    "mode.changed", "scenario.injected", "plant.reset",
    "camera.added", "camera.removed", "camera.configured",
    "camera.calibrated", "door.calibrated", "door.sensor",
    "site.changed", "action.taken", "system.started",
}


def _canonical(record: Dict) -> str:
    """A stable string for hashing — key order must never depend on Python's
    dict ordering or the same record would hash differently on a re-read."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _hash(prev: str, record: Dict) -> str:
    return hashlib.sha256((prev + _canonical(record)).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, directory: Path = AUDIT_DIR):
        self.dir = directory
        self._lock = threading.Lock()
        self._seq = 0
        self._head = GENESIS
        self._loaded = False

    # ------------------------------------------------------------------ state
    def _file_for(self, when: Optional[float] = None) -> Path:
        day = time.strftime("%Y-%m-%d", time.localtime(when or time.time()))
        return self.dir / f"audit-{day}.jsonl"

    def _ensure_loaded(self):
        """Pick up where the previous run left off, so a restart continues the
        same chain instead of starting a fresh one that hides the gap."""
        if self._loaded:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        files = sorted(self.dir.glob("audit-*.jsonl"))
        for line in reversed(list(self._read_lines(files[-1]))) if files else []:
            self._seq = line.get("seq", 0)
            self._head = line.get("hash", GENESIS)
            break
        self._loaded = True

    @staticmethod
    def _read_lines(path: Path) -> Iterator[Dict]:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    yield {"_corrupt": raw[:120]}

    # ----------------------------------------------------------------- append
    def record(self, kind: str, summary: str, actor: str = "system",
               subject: str = "", detail: Optional[Dict] = None) -> Dict:
        """Append one event. Never raises — a failure to log must not take the
        safety platform down with it, but it is reported loudly."""
        try:
            with self._lock:
                self._ensure_loaded()
                now = time.time()
                body = {
                    "seq": self._seq + 1,
                    "ts": round(now, 3),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                    "kind": kind,
                    "actor": actor,
                    "subject": subject,
                    "summary": summary,
                    "detail": detail or {},
                    "prev": self._head,
                }
                body["hash"] = _hash(self._head, body)
                path = self._file_for(now)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(_canonical(body) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())      # survive a hard kill
                self._seq, self._head = body["seq"], body["hash"]
                return body
        except Exception as e:                  # pragma: no cover
            print(f"[audit] could not write '{kind}': {e}")
            return {}

    # ------------------------------------------------------------------ reads
    def entries(self, limit: int = 200, kind: str = "", since: float = 0.0,
                day: str = "") -> List[Dict]:
        self._ensure_loaded()
        files = sorted(self.dir.glob(f"audit-{day}.jsonl" if day else "audit-*.jsonl"))
        out: List[Dict] = []
        for path in reversed(files):            # newest day first
            rows = list(self._read_lines(path))
            for row in reversed(rows):
                if kind and not row.get("kind", "").startswith(kind):
                    continue
                if since and row.get("ts", 0) < since:
                    continue
                out.append(row)
                if len(out) >= limit:
                    return out
        return out

    def verify(self, day: str = "") -> Dict:
        """Walk the chain and report the first record that does not match.

        A clean result means every record still hashes to what the next one
        claims — nothing has been edited, removed or re-ordered in place.
        """
        self._ensure_loaded()
        files = sorted(self.dir.glob(f"audit-{day}.jsonl" if day else "audit-*.jsonl"))
        checked, prev, broken = 0, GENESIS, None
        for path in files:
            for row in self._read_lines(path):
                if "_corrupt" in row:
                    broken = {"file": path.name, "seq": None,
                              "reason": "line is not valid JSON"}
                    break
                stored = row.get("hash", "")
                body = {k: v for k, v in row.items() if k != "hash"}
                if body.get("prev") != prev:
                    broken = {"file": path.name, "seq": row.get("seq"),
                              "reason": "this record does not follow the one before it"}
                    break
                if _hash(prev, body) != stored:
                    broken = {"file": path.name, "seq": row.get("seq"),
                              "reason": "record contents do not match its hash"}
                    break
                prev = stored
                checked += 1
            if broken:
                break
        return {
            "ok": broken is None, "records": checked, "head": prev,
            "files": [f.name for f in files], "broken": broken,
            "note": ("Every record still matches its hash — nothing has been "
                     "edited or removed in place."
                     if broken is None else
                     "The chain breaks here; everything before it is still intact."),
        }

    def head(self) -> Dict:
        """The current chain head — publish this somewhere outside the system
        (a ticket, an email to the fire officer) and the log becomes checkable
        against something the operator cannot rewrite."""
        self._ensure_loaded()
        return {"seq": self._seq, "head": self._head,
                "anchor_advice": "Record this hash outside the platform to make "
                                 "the log verifiable against an independent copy."}

    def stats(self) -> Dict:
        self._ensure_loaded()
        by_kind: Dict[str, int] = {}
        total = 0
        for path in sorted(self.dir.glob("audit-*.jsonl")):
            for row in self._read_lines(path):
                total += 1
                k = row.get("kind", "?")
                by_kind[k] = by_kind.get(k, 0) + 1
        return {"total": total, "by_kind": by_kind, "seq": self._seq,
                "days": len(list(self.dir.glob("audit-*.jsonl")))}


audit = AuditLog()
