"""Rule P2 through ``run_step`` (K = 1): rung 1 on F, group-sequential test on C, bits only to the proposer."""

import json
import tempfile
from pathlib import Path

from p2_fakes import make_run

from cap_evolve import harness  # noqa: E402


def _opt(cfg):
    f = Path(tempfile.mkdtemp()) / "c.json"
    f.write_text(json.dumps(cfg))
    code = ("import sys,pathlib; w=pathlib.Path(sys.argv[1]); "
            "(w/'cfg.json').write_text(pathlib.Path(sys.argv[2]).read_text())")
    return harness.optimizer_from_command(["python3", "-c", code, "{workdir}", str(f)])


def _setup(tmp_path, **kw):
    adapter, run_dir = make_run(tmp_path, n_train=24, n_val=24, default=0.4, **kw)
    base = harness.baseline_pooled(adapter, tmp_path / "seed", run_dir=run_dir, n_trials=4)
    return adapter, run_dir, base


def _step(adapter, run_dir, base, cfg, rejected=None, **gk):
    return harness.run_step(adapter, run_dir=run_dir, parent_dir=run_dir.candidate_dir("seed"),
                            optimizer=_opt(cfg), instructions="x", current_val=base, n_trials=1,
                            rejected=rejected,
                            gate_kwargs={"promotion_rule": "P2", "p2_conf_n": 96, **gk})


def test_a_real_improvement_is_promoted_and_the_loop_continues_from_its_f_measurement(tmp_path):
    adapter, run_dir, base = _setup(tmp_path)
    step = _step(adapter, run_dir, base, {"default": 0.9, "p": {}})
    assert step["accepted"] is True and step["promotion"]["rule"] == "P2"
    assert step["promotion"]["stage"] == "promoted" and step["promotion"]["bit"] == "accept"
    sp = run_dir.read_splits()
    assert {pt["task_id"] for pt in step["candidate_val"]["per_task"]} <= set(sp.feedback)
    assert run_dir.best_id == "cand_0001"


def test_a_harmful_candidate_is_killed_at_rung_1_and_never_touches_C(tmp_path):
    adapter, run_dir, base = _setup(tmp_path)
    n_before = len(adapter.calls)
    step = _step(adapter, run_dir, base, {"default": 0.05, "p": {}})
    assert step["accepted"] is False and step["promotion"]["stage"] == "rung_kill"
    sp = run_dir.read_splits()
    assert not {t for t, _ in adapter.calls[n_before:]} & set(sp.confirm)     # candidate + incumbent top-up only on F


def test_the_proposer_memory_gets_the_bit_not_the_c_numbers(tmp_path):
    adapter, run_dir, base = _setup(tmp_path)
    step = _step(adapter, run_dir, base, {"default": 0.42, "p": {}, "salt": 3})
    reason = step["decision"]["reason"]
    assert "hidden stratum" in reason or "rung 1" in reason
    assert "z " not in reason.split("hidden stratum")[-1]


def test_p2_budget_exhaustion_is_pending_and_stops_the_loop(tmp_path):
    adapter, run_dir, base = _setup(tmp_path, max_metric_calls=0)
    run_dir.update_budget(max_metric_calls=run_dir.spent.metric_calls + 20)
    step = _step(adapter, run_dir, base, {"default": 0.9, "p": {}})
    assert step["accepted"] is False and step["decision"]["indecisive"] is True
    assert run_dir.budget_exhausted()[0] is True
    assert run_dir.spent.metric_calls <= run_dir.budget.max_metric_calls


def test_loop_reports_the_internal_estimate_of_the_champion_on_C(tmp_path):
    adapter, run_dir, base = _setup(tmp_path)
    cfg = {"default": 0.9, "p": {}}
    res = harness.hill_climb_loop(adapter, run_dir=run_dir, optimizer=_opt(cfg), current_val=base,
                                  max_iterations=1, n_trials=1,
                                  gate_kwargs={"promotion_rule": "P2", "p2_conf_n": 96})
    assert res["accepts"] == 1
    est = res["internal_estimate"]
    assert est["mean"] > 0.3 and est["n"] == len(run_dir.read_splits().confirm)
    assert (run_dir.root / "internal_estimate.json").exists()


def test_loop_without_p2_has_no_internal_estimate(tmp_path):
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=24, default=0.4)
    base = harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir, split="val",
                                      tag="seed", n_trials=2)
    res = harness.hill_climb_loop(adapter, run_dir=run_dir, optimizer=_opt({"default": 0.9, "p": {}}),
                                  current_val=base, max_iterations=1, n_trials=2,
                                  gate_kwargs={"mode": "paired", "k_se": 1.0})
    assert "internal_estimate" not in res


def test_c_numbers_and_ids_stay_in_the_private_file_not_in_events_or_the_step_record(tmp_path):
    adapter, run_dir, base = _setup(tmp_path)
    step = _step(adapter, run_dir, base, {"default": 0.9, "p": {}})
    assert "confirm" not in step["promotion"] and step["promotion"]["bit"] == "accept"
    rung_mean = step["promotion"]["rung1"]["mean"]
    assert step["decision"]["delta"] == rung_mean                     # an F number, not the C mean
    events = [json.loads(l) for l in run_dir.events_path.read_text().splitlines()]
    seq = [e for e in events if e["kind"] == "p2_sequential"]
    assert seq and set(seq[0]) - {"kind", "ts", "t", "time"} <= {"round", "candidate", "bit"}
    C = run_dir.read_splits().confirm
    assert not any(f'"{t}"' in run_dir.events_path.read_text() for t in C)
    priv = (run_dir.root / "private" / "p2_confirm.jsonl").read_text()
    assert '"mean"' in priv
    assert not list((run_dir.root / "p2").glob("*")) if (run_dir.root / "p2").exists() else True


def test_the_ledger_of_a_pooled_run_is_not_empty(tmp_path):
    adapter, run_dir, base = _setup(tmp_path)
    _step(adapter, run_dir, base, {"default": 0.9, "p": {}})
    imp = harness._candidate_task_impact(run_dir, "cand_0001", harness.gate_split(run_dir))
    assert imp is not None and "delta" in imp
    out = tmp_path / "w"
    out.mkdir()
    harness._build_ledger(out, run_dir)
    row = [l for l in (out / "LEDGER.md").read_text().splitlines() if l.startswith("| 1 |")]
    assert row and "cand_0001" in row[0]
