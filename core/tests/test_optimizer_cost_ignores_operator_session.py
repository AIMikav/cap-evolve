"""A conductor run bills only the transcripts its own jobs registered: never the operator agent's session
(inherited CLAUDE_CODE_SESSION_ID) and never an unscoped read of every session in the dir."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "core"))

from cap_evolve import Budget, RunDir, optimizer_cost  # noqa: E402

OPERATOR = "0p3r4t0r-0000-0000-0000-000000000000"


def _line(mid, out):
    return json.dumps({"type": "assistant", "timestamp": "2030-01-01T00:00:00Z", "message": {
        "id": mid, "model": "claude-opus-4-8", "usage": {"output_tokens": out}}}) + "\n"


def _world(tmp_path, monkeypatch, *, conductor: bool):
    launch = tmp_path / "launch"
    rd = RunDir.create(launch / "repo" / ".capevolve", ts="s", budget=Budget(max_iterations=5))
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OPERATOR)                # inherited from the operator agent
    monkeypatch.delenv("CAPEVOLVE_OPTIMIZER_TRANSCRIPTS", raising=False)
    proj = cfg / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(launch / "repo"))
    proj.mkdir(parents=True)
    (proj / f"{OPERATOR}.jsonl").write_text(_line("op1", 52_000_000))      # the operator's own heavy usage
    (proj / "someone-else.jsonl").write_text(_line("el1", 7_000_000))
    if conductor:
        (rd.root / "conductor.json").write_text("{}")
    return rd, launch / "repo"


def test_a_conductor_run_does_not_bill_the_operator_session(tmp_path, monkeypatch):
    rd, repo = _world(tmp_path, monkeypatch, conductor=True)
    job = tmp_path / "j00001.transcript.jsonl"
    job.write_text(_line("job1", 40_000))
    assert optimizer_cost.harvest(rd, [repo], 0.0, record=False) is None, "nothing registered: nothing billed"
    optimizer_cost.register(rd, str(job))
    got = optimizer_cost.harvest(rd, [repo], 0.0)
    assert got is not None and got["tokens"] == 40_000, got
    assert OPERATOR not in (rd.root / "optimizer_sessions.json").read_text(), "the inherited id is not recorded"
    assert optimizer_cost.harvest(rd, [repo], 0.0)["tokens"] == 0, "and a second harvest adds nothing"


def test_a_conversational_run_still_bills_the_current_session(tmp_path, monkeypatch):
    rd, repo = _world(tmp_path, monkeypatch, conductor=False)
    got = optimizer_cost.harvest(rd, [repo], 0.0, record=False)
    assert got is not None and got["tokens"] == 52_000_000, "agent mode: the session IS the optimizer (unchanged)"
