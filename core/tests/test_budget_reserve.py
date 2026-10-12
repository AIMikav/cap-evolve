"""D1 budget: rollouts are reserved before dispatch; the run never passes max_metric_calls."""

import threading

import pytest

from p2_fakes import make_candidate, make_run

from cap_evolve import harness  # noqa: E402
from cap_evolve.rundir import BudgetExhausted  # noqa: E402


def test_two_threads_reserving_more_than_the_remainder_exactly_one_wins(tmp_path):
    _, run_dir = make_run(tmp_path, max_metric_calls=100)
    won, lost = [], []

    def go():
        try:
            won.append(run_dir.reserve_metric_calls(60))
        except BudgetExhausted:
            lost.append(1)

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(won) == 1 and len(lost) == 1
    assert run_dir.remaining_metric_calls() == 40
    run_dir.release_metric_calls(won[0])
    assert run_dir.remaining_metric_calls() == 100


def test_update_spent_converts_the_reservation_without_double_counting(tmp_path):
    _, run_dir = make_run(tmp_path, max_metric_calls=100)
    tok = run_dir.reserve_metric_calls(30)
    run_dir.update_spent(metric_calls=30, release=tok)
    assert run_dir.spent.metric_calls == 30
    assert run_dir.reserved_metric_calls() == 0 and run_dir.remaining_metric_calls() == 70


def test_a_dead_holders_reservation_is_freed(tmp_path):
    _, run_dir = make_run(tmp_path, max_metric_calls=100)
    run_dir.reserve_metric_calls(90)
    st = run_dir._read_state()
    for v in st["reserved"].values():
        v["pid"] = 2 ** 22 + 12345          # no such process
    run_dir._write_state(st)
    assert run_dir.remaining_metric_calls() == 100


def test_uncapped_runs_never_refuse(tmp_path):
    _, run_dir = make_run(tmp_path, max_metric_calls=0)
    run_dir.reserve_metric_calls(10 ** 6)
    assert run_dir.remaining_metric_calls() is None and run_dir.can_afford(10 ** 9)


def test_evaluate_raises_before_dispatching_any_rollout(tmp_path):
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=24, max_metric_calls=20)
    with pytest.raises(BudgetExhausted):
        harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir,
                                   split="val", tag="seed", n_trials=1, budget_reserve=True)
    assert adapter.calls == []
    assert run_dir.reserved_metric_calls() == 0


def test_a_failed_evaluation_refunds_its_reservation(tmp_path):
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=10, max_metric_calls=100)

    def boom(*a, **k):
        raise RuntimeError("adapter blew up")

    adapter.run_target = boom
    with pytest.raises(RuntimeError):
        # run_target errors become error rollouts; make the failure escape from live()
        adapter.live = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("live failed"))
        harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir,
                                   split="val", tag="seed", budget_reserve=True)
    assert run_dir.reserved_metric_calls() == 0 and run_dir.spent.metric_calls == 0


def test_a_successful_evaluation_spends_exactly_what_it_reserved(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPEVOLVE_BUDGET_RESERVE", "1")
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=10, max_metric_calls=100)
    harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir,
                               split="val", tag="seed", n_trials=3)
    assert run_dir.spent.metric_calls == 30 and run_dir.reserved_metric_calls() == 0


def test_default_is_off_and_keeps_v1_behaviour(tmp_path, monkeypatch):
    monkeypatch.delenv("CAPEVOLVE_BUDGET_RESERVE", raising=False)
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=24, max_metric_calls=20)
    harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir,
                               split="val", tag="seed")          # overshoots, as v1 did
    assert run_dir.spent.metric_calls == 24


def test_spec_key_turns_it_on(tmp_path, monkeypatch):
    monkeypatch.delenv("CAPEVOLVE_BUDGET_RESERVE", raising=False)
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=24, max_metric_calls=20,
                                spec={"budget_reserve": True})
    with pytest.raises(BudgetExhausted):
        harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir,
                                   split="val", tag="seed")


def test_the_test_split_is_never_reserved(tmp_path):
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=5, n_test=6, max_metric_calls=1)
    run_dir.update_spent(metric_calls=1)
    harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir,
                               split="test", tag="seed_test", budget_reserve=True)
    assert run_dir.spent.metric_calls == 7


# ---- rule P confirmation that does not fit is `unconfirmed`, never a reject or an accept ----------

def _opt(cfg):
    import json
    import tempfile
    from pathlib import Path
    f = Path(tempfile.mkdtemp()) / "c.json"
    f.write_text(json.dumps(cfg))
    code = ("import sys,pathlib; w=pathlib.Path(sys.argv[1]); "
            "(w/'cfg.json').write_text(pathlib.Path(sys.argv[2]).read_text())")
    return harness.optimizer_from_command(["python3", "-c", code, "{workdir}", str(f)])


def test_confirmation_that_does_not_fit_is_unconfirmed_and_nothing_overshoots(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPEVOLVE_BUDGET_RESERVE", "1")
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=24, max_metric_calls=60)
    base = harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir,
                                      split="val", tag="seed", n_trials=1)
    step = harness.run_step(adapter, run_dir=run_dir, parent_dir=run_dir.candidate_dir("seed"),
                            optimizer=_opt({"default": 0.95, "p": {}}), instructions="x",
                            current_val=base, n_trials=1,
                            gate_kwargs={"mode": "paired", "k_se": 1.0, "promotion_rule": "P",
                                         "promotion_n_conf": 5})
    assert step["promotion"]["stage"] == "unconfirmed"
    assert step["accepted"] is False and step["decision"]["indecisive"] is True
    assert "UNCONFIRMED" in step["decision"]["reason"]
    assert run_dir.spent.metric_calls <= 60


def test_replay_of_the_suite_b_overshoot_never_exceeds_the_cap(tmp_path, monkeypatch):
    """The old code read the budget only at iteration start: 49-task evals + 2x147 confirmations ran a
    550 cap to 637. With reservation every call is refused once it cannot fit."""
    monkeypatch.setenv("CAPEVOLVE_BUDGET_RESERVE", "1")
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=49, max_metric_calls=550)
    spent = 0
    for i, n_trials in enumerate([1, 1, 1, 3, 3, 3, 1, 1, 1, 1, 1, 1]):
        try:
            harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir,
                                       split="val", tag=f"c{i}", n_trials=n_trials)
        except BudgetExhausted:
            pass
        spent = run_dir.spent.metric_calls
        assert spent <= 550
