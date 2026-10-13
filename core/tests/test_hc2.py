"""HC-2: K parallel proposals per round on the shared P2 evaluator (opt-in; defaults unchanged).

No model calls: a fake evaluator stands in for ``P2Evaluator`` (whose interface it mirrors) and a fake
optimizer writes deterministic edits.
"""

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "core"))

from cap_evolve import RunDir, harness, hc2  # noqa: E402
from cap_evolve.evaluator import ConfirmVerdict, RoundPlan, RungVerdict  # noqa: E402

F_IDS = [f"f{i}" for i in range(8)]
C_IDS = ["c0", "c1", "c2"]
FEEDBACK = {"f0": "timeout calling search tool", "f1": "timeout calling search tool again",
            "f2": "timeout in search tool", "f3": "wrong date format output",
            "f4": "date format wrong in output", "f5": "missing currency symbol",
            "f6": "currency symbol missing", "f7": "refused unsupported request"}


def _write(run_dir, split, task, tag, k, reward, fb="", split_dir=None):
    d = run_dir.rollouts / (split_dir or split)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task}__{tag}__t{k}.json").write_text(json.dumps(
        {"input": "x", "rollout": {"task_id": task, "output": "o", "error": None},
         "score": {"reward": reward, "feedback": fb}}))


@pytest.fixture
def run_dir(tmp_path):
    from cap_evolve import Budget
    rd = RunDir.create(tmp_path, ts="t", budget=Budget(max_iterations=5))
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "SKILL.md").write_text("base rule one\nbase rule two\n")
    rd.snapshot("seed", seed)
    rd.set_best("seed")
    for t in F_IDS:
        _write(rd, "feedback", t, "seed", 0, 0.0, FEEDBACK[t])
    _write(rd, "feedback", "f0", "seed", 1, 1.0)          # f0 is flaky: a passing draw too
    for t in C_IDS:                                        # C traces leaked into the same directory
        _write(rd, "feedback", t, "seed", 0, 0.0, "secret c failure")
    return rd


class FakeRound:
    champion = None

    def __init__(self, ev, cands):
        self.ev, self.cands = ev, dict(cands)
        ev.opened.append(sorted(self.cands))

    def run_rung(self, rung, map_fn=None):
        return {c: RungVerdict(c, "advance", 0.1, 2.0, 12) for c in self.cands}

    def confirm_jobs(self):
        return [SimpleNamespace(cid=c) for c in self.cands]

    def run_job(self, job):
        pass

    def confirm(self, map_fn=None):
        return self.close_confirm()

    def close_confirm(self):
        first = sorted(self.cands)[0]
        self.champion = first if self.ev.accept else None
        return {c: ConfirmVerdict(c, "accept" if c == self.champion else "reject", 0.1, 2.0, 147)
                for c in self.cands}


class FakeEvaluator:
    proposer_split = "feedback"

    def __init__(self, accept=True, k=4):
        self.opened, self.accept, self.k = [], accept, k

    def final_estimate(self, champion_dir, seed_dir, *, n_trials=4):
        return {"mean": 0.1, "se": 0.05, "n": 3}

    def feedback_ids(self):
        return list(F_IDS)

    def confirm_ids(self):
        return list(C_IDS)

    def plan_round(self, k_wanted, incumbent_dir=None):
        return RoundPlan(min(k_wanted, self.k), 100, True)

    def open_round(self, candidates, incumbent):
        return FakeRound(self, candidates)


def _optimizer(seen):
    lock = threading.Lock()

    def opt(workdir, instructions):
        with lock:
            seen.append((Path(workdir), instructions))
        n = Path(workdir).name
        (Path(workdir) / "SKILL.md").write_text(f"base rule one\nunique edit for {n} " + "x" * len(n) + "\n")
        return {"cost_usd": 0.0, "tokens": 1}
    return opt


def _loop(run_dir, ev, opt, params=None, rounds=1):
    ctx = harness.OptimizerContext()
    return hc2.hc2_loop(None, run_dir=run_dir, optimizer=opt, evaluator=ev,
                        params=params or hc2.HC2Params(k=3, per_cluster=2), ctx=ctx,
                        max_iterations=rounds, workers=3)


def test_k_candidates_submitted_per_round(run_dir):
    ev, seen = FakeEvaluator(), []
    out = _loop(run_dir, ev, _optimizer(seen))
    assert len(ev.opened) == 1 and len(ev.opened[0]) == 3          # K=3 candidates in ONE round
    assert out["accepts"] == 1 and run_dir.best_id == ev.opened[0][0]
    assert len({i for _, i in seen}) == 3                           # distinct clusters, distinct prompts


def test_k_shrinks_to_plan(run_dir):
    ev = FakeEvaluator(k=2)
    _loop(run_dir, ev, _optimizer([]))
    assert len(ev.opened[0]) == 2


def test_c_traces_never_reach_the_proposer(run_dir):
    seen = []
    _loop(run_dir, FakeEvaluator(), _optimizer(seen))
    for wd, instr in seen:
        for sub in ("trajectories", "contrast"):
            for f in (wd / sub).rglob("*") if (wd / sub).exists() else []:
                assert not any(f.name.startswith(c + "__") for c in C_IDS), f
        assert "secret c failure" not in instr
    # and the scrub removes a C file placed there by anything else
    wd = run_dir.root / "work" / "x" / "trajectories"
    wd.mkdir(parents=True)
    (wd / "c1__seed__t0.json").write_text("{}")
    (wd / "f1__seed__t0.json").write_text("{}")
    assert hc2.scrub_confirm_traces(wd.parent, C_IDS) == 1 and (wd / "f1__seed__t0.json").exists()


def test_read_traces_filters_to_feedback_ids(run_dir):
    tr = hc2.read_traces(run_dir, ("feedback",), F_IDS, hc2.incumbent_tag_ok("seed"))
    assert {t.task_id for t in tr} <= set(F_IDS)


def test_contrastive_pair_for_flaky_task(run_dir):
    tr = hc2.read_traces(run_dir, ("feedback",), F_IDS, hc2.incumbent_tag_ok("seed"))
    pairs = hc2.contrastive_pairs(tr, set(F_IDS), 3)
    assert [(g.task_id, g.reward, b.reward) for g, b in pairs] == [("f0", 1.0, 0.0)]


def test_clusters_group_similar_failures(run_dir):
    tr = hc2.read_traces(run_dir, ("feedback",), F_IDS, hc2.incumbent_tag_ok("seed"))
    cl = hc2.cluster_failures(hc2.failing_tasks(tr), F_IDS, 0.3)
    assert len(cl) >= 3
    assert any({"f3", "f4"} <= set(c) for c in cl)


def test_diversity_gate_rejects_near_duplicate(tmp_path):
    parent = tmp_path / "p"
    parent.mkdir()
    (parent / "SKILL.md").write_text("a\nb\nc\n")
    c1, c2, c3 = (tmp_path / n for n in ("c1", "c2", "c3"))
    for c, body in ((c1, "a\nb\nc\nuse the guard on country\nvalidate exchange items\n"),
                    (c2, "a\nb\nc\nuse the guard on country\nvalidate exchange items now\n"),
                    (c3, "a\nb\nc\nformat dates as iso\n")):
        c.mkdir()
        (c / "SKILL.md").write_text(body)
    d1, d2, d3 = (hc2.diff_lines(parent, c) for c in (c1, c2, c3))
    assert hc2.near_duplicate(d2, [d1], 0.3) == 0
    assert hc2.near_duplicate(d3, [d1], 0.3) is None
    assert hc2.near_duplicate(d1, [d1], 0.99) == 0


def test_loop_drops_duplicate_of_rejected_diff_at_no_rollout_cost(run_dir):
    ev = FakeEvaluator(accept=False)

    def same_edit(workdir, instructions):
        (Path(workdir) / "SKILL.md").write_text("base rule one\nthe same edit everywhere\n")

    _loop(run_dir, ev, same_edit, rounds=1)      # round 1: siblings duplicate each other -> one live
    assert len(ev.opened[0]) == 1
    n_before = len(ev.opened)
    _loop(run_dir, ev, same_edit, rounds=1)      # round 2: now a near-duplicate of a rejected diff
    assert len(ev.opened) == n_before            # nothing sent to the evaluator


def test_consolidation_mode_is_opt_in(run_dir):
    seen = []
    _loop(run_dir, FakeEvaluator(), _optimizer(seen), hc2.HC2Params(k=2, consolidate=True))
    assert all((wd / "cross_trace_summary.md").exists() and "Consolidation mode" in i for wd, i in seen)
    seen.clear()
    _loop(run_dir, FakeEvaluator(), _optimizer(seen), hc2.HC2Params(k=2))
    assert not any((wd / "cross_trace_summary.md").exists() or "Consolidation mode" in i for wd, i in seen)


def test_defaults_unchanged():
    assert hc2.HC2Params.from_spec({}) is None
    assert hc2.HC2Params.from_spec({"hc2_k": 3}) is None          # off unless hc2: true
    with pytest.raises(ValueError):
        hc2.HC2Params.from_spec({"hc2": True})                    # needs P2 + pooled
    p = hc2.HC2Params.from_spec({"hc2": True, "promotion_rule": "P2", "split_mode": "pooled",
                                 "hc2_k": 5, "hc2_consolidate": True})
    assert p.k == 5 and p.consolidate and p.retries == 1


def test_hill_climb_cli_has_hc2_off_by_default():
    import importlib.util
    path = REPO / "skills/algorithms/hill-climb/scripts/run.py"
    src = path.read_text()
    assert '"--hc2", default=None' in src and "if args.hc2:" in src


def test_workers_forwarded_to_baseline_and_algorithm(monkeypatch, tmp_path):
    src = (REPO / "core/cap_evolve/cli.py").read_text()
    assert 'base_cmd += ["--workers"' in src and 'alg_cmd += ["--workers"' in src
    assert 'spec.get("workers")' in src
    bsrc = (REPO / "skills/phases/baseline/scripts/run.py").read_text()
    assert "workers=args.workers" in bsrc


def test_baseline_passes_workers_to_evaluations(tmp_path, monkeypatch):
    from cap_evolve import Budget
    rd = RunDir.create(tmp_path, ts="b", budget=Budget(max_iterations=1))
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "f.txt").write_text("x")
    seen = []

    def fake_eval(adapter, cdir, **kw):
        seen.append((kw["split"], kw.get("workers")))
        from cap_evolve.loop import SplitResult
        return SplitResult(split=kw["split"], reward=0.5, stderr=0.0, n_tasks=1, n_scored=1)

    monkeypatch.setattr(harness, "evaluate_candidate", fake_eval)
    monkeypatch.setattr(RunDir, "read_splits", lambda self: SimpleNamespace(train=["a"], val=["b"], test=["c"]))
    harness.baseline(None, seed, run_dir=rd, workers=4)
    assert seen == [("val", 4), ("train", 4)]


# ---- against the real P2 evaluator --------------------------------------------------------------

def test_hc2_round_on_the_real_evaluator(tmp_path):
    from p2_fakes import make_run
    from cap_evolve import evaluator, harness as h, promotion
    adapter, rd = make_run(tmp_path, n_train=24, n_val=24, default=0.4, max_metric_calls=3000)
    h.baseline_pooled(adapter, tmp_path / "seed", run_dir=rd, n_trials=2)
    params = promotion.P2Params.from_spec({"promotion_rule": "P2", "p2_conf_n": 96, "p2_ctrl_m": 4})
    ev = evaluator.P2Evaluator(adapter, rd, params)
    seen = []

    def opt(workdir, instructions):
        seen.append(Path(workdir))
        i = len(seen)
        cfg = json.loads((Path(workdir) / "cfg.json").read_text())
        cfg.update(default=0.85 if i == 1 else 0.4, salt=i)        # only the first proposal helps
        (Path(workdir) / "cfg.json").write_text(json.dumps(cfg, indent=1))

    out = hc2.hc2_loop(adapter, run_dir=rd, optimizer=opt, evaluator=ev,
                       params=hc2.HC2Params(k=3, per_cluster=2), ctx=harness.OptimizerContext(),
                       max_iterations=1, workers=2)
    assert len(seen) >= 3 and out["iterations"] >= 3
    assert out["accepts"] == 1 and rd.best_id != "seed"
    # FINAL phase: champion vs seed on C, written to summary.json, inside max_metric_calls, paid once
    est = out["internal_estimate"]
    assert est["n"] and json.loads((rd.root / "summary.json").read_text())["internal_estimate"] == est
    assert rd.spent.metric_calls <= 3000
    paid = rd.spent.metric_calls
    assert hc2.final_estimate(ev, rd) == est and rd.spent.metric_calls == paid
    c_ids = set(ev.confirm_ids())
    for wd in seen:
        for f in (wd / "trajectories").glob("*.json"):
            assert f.name.split("__")[0] not in c_ids


def test_split_to_k_gives_distinct_slices_when_clusters_are_few():
    out = hc2.split_to_k([["a", "b", "c", "d"]], 3)
    assert len(out) == 3 and sorted(sum(out, [])) == ["a", "b", "c", "d"]
    assert hc2.split_to_k([["a"]], 3) == [["a"]]


def test_final_estimate_skipped_when_unfunded_and_never_over_budget(tmp_path):
    from p2_fakes import make_run
    from cap_evolve import evaluator, harness as h, promotion
    adapter, rd = make_run(tmp_path, n_train=24, n_val=24, default=0.4, max_metric_calls=100)
    h.baseline_pooled(adapter, tmp_path / "seed", run_dir=rd, n_trials=2)
    ev = evaluator.P2Evaluator(adapter, rd, promotion.P2Params.from_spec({"promotion_rule": "P2"}))
    assert hc2.final_estimate(ev, rd) is None                    # champion is the seed: nothing to estimate
    rd.snapshot("cand_0001", tmp_path / "seed")
    rd.set_best("cand_0001")
    out = hc2.final_estimate(ev, rd)
    assert out["skipped"] == "budget" and rd.spent.metric_calls <= 100
