"""Persistence for the site document.

The site lives in `data/site.json`. It is written atomically (temp file then
replace) because the operator can be editing the building while the plant
engine is reading it several times a second — a half-written file would take
the whole platform down.

Every successful save bumps a revision counter and notifies listeners, which
is how the running engine learns that rooms appeared, moved or disappeared.
"""
from __future__ import annotations
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from .model import Site
from .presets import PRESETS

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
SITE_FILE = DATA_DIR / "site.json"
BACKUP_DIR = DATA_DIR / "site_backups"
PLANS_DIR = ROOT / "web" / "assets" / "plans"

# What a fresh install gets. The compliance brief this platform targets is a
# hospital, so that is the site it opens with; `industrial` is one click away
# in the editor for anyone using it on a plant.
DEFAULT_PRESET = os.environ.get("SAFEX_PRESET", "hospital")


class SiteStore:
    def __init__(self, path: Path = SITE_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._site: Optional[Site] = None
        self._rev = 0
        self._listeners: List[Callable[[Site], None]] = []

    # ------------------------------------------------------------- lifecycle
    @property
    def site(self) -> Site:
        with self._lock:
            if self._site is None:
                self._site = self._load()
            return self._site

    @property
    def rev(self) -> int:
        return self._rev

    def on_change(self, fn: Callable[[Site], None]) -> None:
        """Register a listener (the plant engine) for layout changes."""
        self._listeners.append(fn)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn(self.site)
            except Exception as e:                       # never break a save
                print(f"[site] listener failed: {e}")

    # ------------------------------------------------------------------- io
    def _load(self) -> Site:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                site = Site.from_dict(data)
                if site.rooms:
                    return site
                print("[site] stored site has no rooms — falling back to preset")
            except Exception as e:
                print(f"[site] could not read {self.path.name}: {e} — using preset")
        site = PRESETS.get(DEFAULT_PRESET, PRESETS["hospital"])()
        self._write(site)
        return site

    def _write(self, site: Site) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(site.as_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)          # atomic: readers never see a partial file

    def _backup(self) -> None:
        """Keep the last few versions — an operator who deletes the wrong floor
        during a demo should not lose the building."""
        if not self.path.exists():
            return
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            shutil.copy2(self.path, BACKUP_DIR / f"site-{stamp}.json")
            backups = sorted(BACKUP_DIR.glob("site-*.json"))
            for old in backups[:-10]:
                old.unlink(missing_ok=True)
        except Exception:
            pass

    # ---------------------------------------------------------------- saving
    def save(self, site: Optional[Site] = None, backup: bool = True) -> dict:
        """Validate, persist and publish a site. Returns the problem list so a
        caller can surface anything wrong without blocking the save."""
        with self._lock:
            target = site or self.site
            target.rebuild()
            if backup:
                self._backup()
            self._write(target)
            self._site = target
            self._rev += 1
            rev = self._rev
        problems = target.problems()
        try:
            from ..core.audit import audit
            audit.record("site.changed",
                         f"Site plan saved — {len(target.floors)} floors, "
                         f"{len(target.rooms)} rooms, {len(target.markers)} assets",
                         actor="operator",
                         detail={"rev": rev, "site": target.name,
                                 "problems": len(problems)})
        except Exception:
            pass
        self._notify()
        return {"ok": True, "rev": rev, "problems": problems}

    def mutate(self, fn: Callable[[Site], None]) -> dict:
        """Apply an edit under the lock, then save."""
        with self._lock:
            site = self.site
            fn(site)
        return self.save(site)

    def apply_preset(self, name: str) -> dict:
        maker = PRESETS.get(name)
        if not maker:
            return {"ok": False, "error": f"unknown preset {name}"}
        with self._lock:
            self._backup()
            site = maker()
            self._site = site
        res = self.save(site, backup=False)
        res["preset"] = name
        return res

    def reload(self) -> dict:
        with self._lock:
            self._site = self._load()
            self._rev += 1
        self._notify()
        return {"ok": True, "rev": self._rev}


site_store = SiteStore()
