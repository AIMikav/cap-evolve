"""AO-2 with promotion_rule P2: the conductor drives the shared P2Evaluator (gate PR-1) as rounds of K
candidates through a shared slot pool. Real evaluator and run dir, seeded Bernoulli adapter, no model calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import ao2_fakes as F  # noqa: E402

import conductor  # noqa: E402
from cap_evolve import conductor_state as cs  # noqa: E402
from cap_evolve import graph  # noqa: E402

SPEC = {"promotion_rule": "P2", "p2_conf_n": 48, "p2_ctrl_m": 2, "split_mode": "pooled", "ao_plateau_n": 99}


def _make(tmp_path, effects, *, cap=6000, k=3, slots=4, spec=None, **bk):
    be = F.P2Backend(tmp_path, effects, max_metric_calls=cap, workers=slots, **bk)
    cfg = conductor.Cfg.from_spec({**SPEC, "max_metric_calls": cap, **(spec or {})})
    cfg.k_prop, cfg.slots, cfg.tick_s = k, slots, 0
    return be, cfg


def _run(be, cfg, ticks=3000, crash_at=()):
    c = conductor.Conductor(be.run_dir.root, be, cfg, sleep=lambda s: None)
    import time
    for i in range(ticks):
        if i in crash_at:
            be.pool.shutdown(wait=True)
            be = be.attach(None)
            c = conductor.Conductor(be.run_dir.root, be, cfg, sleep=lambda s: None)
        if c.tick():
            return c, be
        time.sleep(0.002)
    raise AssertionError("did not finish")


def test_p2_round_picks_the_real_improvement_in_parallel(tmp_path):
    be, cfg = _make(tmp_path, [0.0, 0.45, 0.0, -0.2, 0.0, 0.0], cap=7000, k=3)
    c, be = _run(be, cfg)
    assert c.champion != "seed", "a +0.45 candidate must be promoted by a confirmation on C"
    assert be.max_parallel >= 2, "chunked jobs of several candidates share the slot pool"
    assert be.run_dir.spent.metric_calls <= 7000
    assert c.st["rounds_done"] >= 1
    nodes = {r["id"]: r for r in graph.read_nodes(be.run_dir)}
    blob = json.dumps(list(nodes.values()))
    assert '"mean"' not in blob and '"z"' not in blob and "confirm_mean" not in blob, "C aggregates stay out of graph.jsonl"
    assert any(n.get("confirmation") == "accept" for n in nodes.values())
    assert be.finalized()


def test_p2_k1_uses_the_group_sequential_test_without_blocking_the_tick(tmp_path):
    be, cfg = _make(tmp_path, [0.45, 0.0, 0.0], cap=5000, k=1, slots=2)
    c = conductor.Conductor(be.run_dir.root, be, cfg, sleep=lambda s: None)
    import time
    seq_seen = False
    for _ in range(3000):
        done = c.tick()
        r = c.st.get("round")
        seq_seen = seq_seen or bool(r and r["stage"] == "seq")
        if done:
            break
        time.sleep(0.002)
    assert seq_seen and c.champion != "seed"
    assert be.run_dir.spent.metric_calls <= 5000


def test_p2_budget_cap_never_exceeded_and_unfunded_is_pending_not_reject(tmp_path):
    be, cfg = _make(tmp_path, [0.45, 0.45, 0.3, 0.0], cap=900, k=2)
    c, be = _run(be, cfg)
    assert be.run_dir.spent.metric_calls <= 900
    rejected_without_confirmation = [t for t, n in c.st["nodes"].items()
                                     if n["status"] == "rejected" and n.get("why") != "confirm_reject"]
    assert not rejected_without_confirmation


def test_p2_resume_after_a_crash_reaches_the_same_champion(tmp_path):
    effects = [0.0, 0.45, 0.0, -0.2, 0.0, 0.0]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    be0, cfg0 = _make(tmp_path / "a", effects, cap=7000, k=3)
    c0, be0 = _run(be0, cfg0)
    be1, cfg1 = _make(tmp_path / "b", effects, cap=7000, k=3)
    c1, be1 = _run(be1, cfg1, crash_at={6, 14})
    assert c1.champion == c0.champion
    assert be1.run_dir.spent.metric_calls <= 7000
