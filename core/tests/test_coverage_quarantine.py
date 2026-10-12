"""D1 coverage: retry + quarantine the incumbent's holes, coverage on the evaluable set, floor +
tipping-point check instead of the 0.95 veto. Includes an offline replay of the hill-climb run in which
11 of 12 screen-passing candidates were vetoed at 92.5% joint coverage."""

import json
from pathlib import Path

import pytest

from p2_fakes import make_candidate, make_run

from cap_evolve import coverage as cov  # noqa: E402
from cap_evolve import gate, harness  # noqa: E402
from cap_evolve.rundir import RunDir  # noqa: E402

FIX = json.loads((Path(__file__).parent / "fixtures" / "a_s2_coverage.json").read_text())


def _rows(d):
    return [{"task_id": t, "reward": r, "raw": {"valid_trials": v, "errored": not v}} for t, (r, v) in d.items()]


# ---- replay: the 40-task val split, 3 incumbent holes, 11 screen-passing candidates ---------------

def test_replay_baseline_has_three_holes_and_every_candidate_is_vetoed_by_v1():
    base = _rows(FIX["baseline"])
    holes = cov.missing_ids(base, FIX["baseline"])
    assert len(holes) == 3
    for cid, row in FIX["candidates"].items():
        cand = _rows(row)
        assert cov.joint_coverage_on(FIX["baseline"], base, cand) == pytest.approx(37 / 40)
    assert len(FIX["candidates"]) == 11


def test_replay_quarantine_lets_all_eleven_through_to_the_screen():
    base = _rows(FIX["baseline"])
    E = cov.evaluable_set(FIX["baseline"], cov.missing_ids(base, FIX["baseline"]))
    assert len(E) == 37
    passed = 0
    for cid, row in FIX["candidates"].items():
        cand = _rows(row)
        assert cov.joint_coverage_on(E, base, cand) == 1.0
        d = cov.paired_deltas_on(E, base, cand)
        dec = gate.decide(0.0, 0.0, paired_deltas=d, mode="paired", k_se=1.0, coverage=1.0,
                          min_coverage=0.75)
        assert dec.indecisive is False
        passed += bool(dec.accept)
    assert passed >= 9          # the real screen z were 2.2 .. 4.4; two weak ones may fall on 37 tasks


def test_replay_without_quarantine_the_tipping_check_clears_all_but_the_weakest():
    """Floor 0.75 + tipping instead of the veto: impute the 3 missing pairs at -0.5."""
    base = _rows(FIX["baseline"])
    E = list(FIX["baseline"])
    flips = 0
    for cid, row in FIX["candidates"].items():
        cand = _rows(row)
        d = cov.paired_deltas_on(E, base, cand)
        assert cov.joint_coverage_on(E, base, cand) >= 0.75
        tp = cov.tipping_point(d, 3, impute=-0.5, k_se=1.0)
        flips += tp["flips"]
    assert flips <= 3 and flips < len(FIX["candidates"])


# ---- units --------------------------------------------------------------------------------------

def test_tipping_point_flags_a_verdict_the_missing_cells_could_change():
    d = [0.2, 0.1, 0.3, 0.0, 0.1, 0.2, 0.1, 0.2] * 3         # passes
    assert cov.tipping_point(d, 0)["flips"] is False
    assert cov.tipping_point(d, 1)["flips"] is False
    tp = cov.tipping_point(d, 14)                            # fourteen half-task regressions flip it
    assert tp["base_pass"] and not tp["imputed_pass"] and tp["flips"]


def test_differential_missingness_counts_excess_candidate_errors_as_failures():
    E = [f"t{i}" for i in range(20)]
    cur = _rows({t: (1.0, 1) for t in E})
    cand_rows = {t: (1.0, 1) for t in E}
    for t in E[:5]:
        cand_rows[t] = (0.0, 0)
    cand = _rows(cand_rows)
    assert cov.differential_failures(E, cur, cand) == E[:5]          # 5 > max(2, 1)
    cand_rows2 = {t: (1.0, 1) for t in E}
    cand_rows2[E[0]] = (0.0, 0)
    assert cov.differential_failures(E, cur, _rows(cand_rows2)) == []   # within the allowance
    # the incumbent erroring on as many tasks cancels the excess
    cur2 = _rows({**{t: (1.0, 1) for t in E}, **{t: (0.0, 0) for t in E[5:10]}})
    cand3 = _rows({**{t: (1.0, 1) for t in E[5:]}, **{t: (0.0, 0) for t in E[:5]}})
    d = cov.paired_deltas_on(E, cur, cand, failures=E[:5])
    assert d.count(-1.0) == 5 and len(d) == 20


def test_floor_gives_indecisive_below_075():
    d = gate.decide(0.5, 0.9, paired_deltas=[0.4] * 10, mode="paired", coverage=0.7, min_coverage=0.75)
    assert d.indecisive and not d.accept
    ok = gate.decide(0.5, 0.9, paired_deltas=[0.4, 0.3, 0.5, 0.4] * 3, mode="paired", coverage=0.8,
                     min_coverage=0.75)
    assert ok.accept and not ok.indecisive


# ---- integration through run_step -----------------------------------------------------------------

def _opt(cfg):
    import tempfile
    f = Path(tempfile.mkdtemp()) / "c.json"
    f.write_text(json.dumps(cfg))
    code = ("import sys,pathlib; w=pathlib.Path(sys.argv[1]); "
            "(w/'cfg.json').write_text(pathlib.Path(sys.argv[2]).read_text())")
    return harness.optimizer_from_command(["python3", "-c", code, "{workdir}", str(f)])


def _setup_holes(tmp_path, n_holes=3):
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=40, default=0.4)
    holes = [f"t{i:03d}" for i in range(n_holes)]
    seed = run_dir.candidate_dir("seed")
    (seed / "cfg.json").write_text(json.dumps({"default": 0.4, "p": {}, "err": holes}))
    base = harness.evaluate_candidate(adapter, seed, run_dir=run_dir, split="val", tag="seed")
    return adapter, run_dir, base, holes


def _step(adapter, run_dir, base, cfg, **gk):
    return harness.run_step(adapter, run_dir=run_dir, parent_dir=run_dir.candidate_dir("seed"),
                            optimizer=_opt(cfg), instructions="x", current_val=base, n_trials=1,
                            gate_kwargs={"mode": "paired", "k_se": 1.0, "min_coverage": 0.95,
                                         "gate_coverage": "joint", **gk})


def test_v1_default_vetoes_on_the_incumbents_holes(tmp_path, monkeypatch):
    monkeypatch.delenv("CAPEVOLVE_COVERAGE_POLICY", raising=False)
    adapter, run_dir, base, holes = _setup_holes(tmp_path)
    step = _step(adapter, run_dir, base, {"default": 0.95, "p": {}})
    assert step["decision"]["indecisive"] is True and step["accepted"] is False


def test_tipping_policy_retries_quarantines_and_judges(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPEVOLVE_COVERAGE_POLICY", "tipping")
    adapter, run_dir, base, holes = _setup_holes(tmp_path)
    healed = harness.retry_missing(adapter, run_dir, base, candidate_dir=run_dir.candidate_dir("seed"),
                                   tag="seed", quarantine=True)
    assert set(run_dir.quarantined_tasks()) == set(holes)         # the hole is the incumbent's own
    kinds = [json.loads(l)["kind"] for l in run_dir.events_path.read_text().splitlines()]
    assert kinds.count("missing_retry") == 2 and "task_quarantined" in kinds
    step = _step(adapter, run_dir, healed, {"default": 0.95, "p": {}})
    assert step["decision"]["indecisive"] is False and step["accepted"] is True
    assert step["coverage_check"]["joint_coverage"] == 1.0 and step["coverage_check"]["evaluable"] == 37


def test_a_retry_that_recovers_a_cell_does_not_quarantine_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPEVOLVE_COVERAGE_POLICY", "tipping")
    adapter, run_dir, base, holes = _setup_holes(tmp_path, n_holes=2)
    # the infra error was transient: the incumbent's cfg no longer errors on retry
    seed = run_dir.candidate_dir("seed")
    (seed / "cfg.json").write_text(json.dumps({"default": 0.4, "p": {}}))
    healed = harness.retry_missing(adapter, run_dir, base, candidate_dir=seed, tag="seed", quarantine=True)
    assert run_dir.quarantined_tasks() == []
    assert all(pt["raw"]["valid_trials"] >= 1 for pt in healed.per_task)


def test_tipping_policy_counts_candidate_crashes_as_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPEVOLVE_COVERAGE_POLICY", "tipping")
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=40, default=0.9)
    base = harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir,
                                      split="val", tag="seed")
    crashes = [f"t{i:03d}" for i in range(12)]                  # 30% of the split: not exogenous
    step = _step(adapter, run_dir, base, {"default": 0.9, "p": {}, "err": crashes})
    assert step["coverage_check"]["counted_as_failures"], step["coverage_check"]
    assert step["accepted"] is False


def test_policy_validation():
    from cap_evolve.gatecfg import GateConfig
    with pytest.raises(ValueError):
        GateConfig.from_mapping({"coverage_policy": "nope"}, environ={})
    assert GateConfig.from_mapping({}, environ={}).coverage_policy == "veto"
    assert GateConfig.from_mapping({}, environ={"CAPEVOLVE_COVERAGE_POLICY": "tipping"}).coverage_policy == "tipping"
