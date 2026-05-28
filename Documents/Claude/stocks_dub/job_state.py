"""
Persistent job state for long-running background tasks.
Saves to disk so results survive tab switches, browser refreshes, and Streamlit reruns.

Usage:
    js = JobState("growth_report")
    js.start(total=500)
    js.update(done=42, current="AAPL")
    js.finish(result=rows)

    # On page load:
    js = JobState("growth_report")
    if js.is_running():   ...show progress...
    elif js.is_done():    ...show results...
"""

import json
import os
import threading
from datetime import datetime

_DIR  = os.path.dirname(os.path.abspath(__file__))
_LOCK = threading.Lock()


def _path(job_id: str) -> str:
    return os.path.join(_DIR, f".job_{job_id}.json")


class JobState:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self._path  = _path(job_id)

    # ── Read ──────────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        with _LOCK:
            try:
                with open(self._path) as f:
                    return json.load(f)
            except Exception:
                return {"status": "idle"}

    def _save(self, data: dict):
        with _LOCK:
            with open(self._path, "w") as f:
                json.dump(data, f, indent=2)

    def state(self) -> dict:
        return self._load()

    def is_running(self) -> bool:
        return self._load().get("status") in ("running", "cancelling")

    def is_done(self) -> bool:
        return self._load().get("status") in ("done", "cancelled")

    def is_idle(self) -> bool:
        return self._load().get("status") in ("idle", None)

    def is_cancelling(self) -> bool:
        return self._load().get("status") == "cancelling"

    def was_cancelled(self) -> bool:
        return self._load().get("status") == "cancelled"

    def progress(self) -> tuple[int, int, str]:
        """Returns (done, total, current_ticker). total=0 means unknown."""
        d = self._load()
        return d.get("done", 0), d.get("total", 0), d.get("current", "")

    def result(self):
        return self._load().get("result")

    def started_at(self) -> str:
        return self._load().get("started_at", "")

    def completed_at(self) -> str:
        return self._load().get("completed_at", "")

    def meta(self) -> dict:
        return self._load().get("meta", {})

    # ── Write ─────────────────────────────────────────────────────────────────
    def start(self, total: int, meta: dict = None):
        self._save({
            "status":     "running",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total":      total,
            "done":       0,
            "current":    "",
            "meta":       meta or {},
            "result":     None,
        })

    def update(self, done: int, current: str = ""):
        d = self._load()
        d["status"]  = "running"   # always reassert — guards against race condition
        d["done"]    = done
        d["current"] = current
        self._save(d)

    def finish(self, result):
        d = self._load()
        # Respect cancellation — mark as cancelled instead of done
        d["status"]       = "cancelled" if d.get("status") == "cancelling" else "done"
        d["done"]         = d.get("total", 0)
        d["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        d["result"]       = result
        self._save(d)

    def cancel(self):
        """
        Request cancellation of a running job.
        Workers check is_cancelling() and stop early — they are NOT killed forcefully.
        Results collected so far are preserved.
        """
        d = self._load()
        if d.get("status") == "running":
            d["status"] = "cancelling"
            self._save(d)

    def fail(self, error: str):
        d = self._load()
        d["status"] = "failed"
        d["error"]  = error
        self._save(d)

    def reset(self):
        if os.path.exists(self._path):
            os.remove(self._path)
