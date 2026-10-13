"""conductor_state -- ``$R/conductor.json``: the AO-2 conductor's durable job table and budget books.

The conductor (skills/algorithms/agent-optimize/scripts/conductor.py) is a tick loop that is a pure
function of files; this module owns the one file that is not derivable from the evidence ledger
(``eval_index.jsonl``) or the candidate graph (``graph.jsonl``): which jobs exist, which are
running, and what each has RESERVED against the rollout and token budgets.

    {version, epoch, champion, n_proposals, proposals_since_improve, next_job, finalized, stopped,
     started, jobs: {job_id: Job}, nodes: {tag: Node}}

A job: ``{id, kind: eval|propose|merge|diagnose, tag, state: queued|running|done|failed|lost,
reserved_rollouts, reserved_tokens, ...payload}``. Reservation arithmetic is the whole point:
before a job starts, ``committed + reserved + need + finalize_reserve <= cap`` must hold, so
parallel jobs cannot overspend a budget the way a check that runs at verb start can.

Writes are atomic (tmp + ``os.replace``) under ``_file_lock``; a SIGKILL leaves the old file whole.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .rundir import _atomic_write, _file_lock

FILENAME = "conductor.json"
VERSION = 1
LIVE = ("queued", "running")


def fresh() -> dict:
    return {"version": VERSION, "epoch": 0, "champion": "seed", "n_proposals": 0,
            "proposals_since_improve": 0, "next_job": 1, "finalized": False, "stopped": None,
            "started": time.time(), "tokens_committed": 0, "rollouts_base": 0, "jobs": {}, "nodes": {}}


class Store:
    """Load / mutate / save ``conductor.json``. ``update`` is the only write path."""

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / FILENAME
        self._lock = self.root / ".conductor.lock"

    def load(self) -> dict:
        try:
            st = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return fresh()
        base = fresh()
        base.update(st)
        return base

    def save(self, st: dict) -> None:
        with _file_lock(self._lock):
            _atomic_write(self.path, json.dumps(st, indent=1, sort_keys=True, default=str))

    def update(self, fn) -> dict:
        """Read-modify-write under the lock; ``fn(state)`` mutates in place."""
        with _file_lock(self._lock):
            st = self.load()
            fn(st)
            _atomic_write(self.path, json.dumps(st, indent=1, sort_keys=True, default=str))
        return st


# ---- reservation arithmetic (pure) ------------------------------------------------------------

def reserved(st: dict, key: str = "reserved_rollouts") -> int:
    """Rollouts (or tokens) held back by live jobs, plus - for rollouts - what each confirming
    node reserved up front for the chunks it has not launched yet."""
    n = sum(int(j.get(key) or 0) for j in st["jobs"].values() if j.get("state") in LIVE)
    if key == "reserved_rollouts":
        n += sum(int(x.get("reserved") or 0) for x in st.get("nodes", {}).values())
    return n


def running(st: dict, kind: str | None = None) -> list[dict]:
    return [j for j in st["jobs"].values() if j.get("state") == "running" and (kind is None or j["kind"] == kind)]


def live(st: dict, kind: str | None = None) -> list[dict]:
    return [j for j in st["jobs"].values() if j.get("state") in LIVE and (kind is None or j["kind"] == kind)]


def rollouts_fit(st: dict, committed: int, need: int, cap: int | None, finalize_reserve: int = 0) -> bool:
    """May ``need`` more rollouts be reserved? ``cap`` None/0 = unbounded."""
    if not cap:
        return True
    return committed + reserved(st) + need + finalize_reserve <= cap


def tokens_fit(st: dict, need: int, cap: int | None, finalize_tokens: int = 0) -> bool:
    if not cap:
        return True
    return int(st.get("tokens_committed") or 0) + reserved(st, "reserved_tokens") + need + finalize_tokens <= cap


def new_job(st: dict, kind: str, **fields) -> dict:
    jid = f"j{st['next_job']:05d}"
    st["next_job"] += 1
    job = {"id": jid, "kind": kind, "state": "queued", "created": time.time(), **fields}
    st["jobs"][jid] = job
    return job
