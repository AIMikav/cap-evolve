"""D1 ledger: at n<3 trials per task the ledger lists no causal broke/fixed, only flip counts against
the null expectation; every draw of a capability hash is pooled."""

import json
import math

import pytest

from p2_fakes import make_candidate, make_run

from cap_evolve import harness  # noqa: E402


def test_null_flip_expectation_matches_two_p_one_minus_p_at_n1():
    assert harness.null_flip_expectation([0.5] * 10, 1) == pytest.approx(10 * 0.5)
    assert harness.null_flip_expectation([0.9, 0.1], 1) == pytest.approx(2 * 2 * 0.09)
    assert harness.null_flip_expectation([0.0, 1.0], 3) == 0.0
    assert harness.null_flip_expectation([0.5], 3) > harness.null_flip_expectation([0.5], 1) * 0.5


def _two_identical_draws(tmp_path):
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=49, default=0.55)
    seed = run_dir.candidate_dir("seed")
    harness.evaluate_candidate(adapter, seed, run_dir=run_dir, split="val", tag="seed")
    # a byte-identical copy measured under a fresh tag (the null control)
    run_dir.snapshot("cand_0001", seed)
    harness.evaluate_candidate(adapter, run_dir.candidate_dir("cand_0001"), run_dir=run_dir, split="val",
                               tag="cand_0001")
    run_dir.log_event("step", candidate="cand_0001", parent="seed", accept=False, val=0.5, parent_val=0.5)
    return adapter, run_dir


def test_default_still_lists_flips_as_broke_fixed(tmp_path, monkeypatch):
    monkeypatch.delenv("CAPEVOLVE_LEDGER_NOISE_GUARD", raising=False)
    _, run_dir = _two_identical_draws(tmp_path)
    imp = harness._candidate_task_impact(run_dir, "cand_0001", "val")
    assert "causal" not in imp and (imp["broke"] or imp["fixed"])      # v1: noise reads as causation


def _behaviourally_identical(tmp_path):
    """Different bytes (a salt that only reseeds the draws), same success probabilities."""
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=49, default=0.55)
    harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir, split="val", tag="seed")
    d = make_candidate(tmp_path, "c1", {"default": 0.55, "p": {}, "salt": 1})
    run_dir.snapshot("cand_0001", d)
    harness.evaluate_candidate(adapter, run_dir.candidate_dir("cand_0001"), run_dir=run_dir, split="val",
                               tag="cand_0001")
    run_dir.log_event("step", candidate="cand_0001", parent="seed", accept=False, val=0.5, parent_val=0.5)
    return adapter, run_dir


def test_guard_reports_counts_not_causes_at_n_below_3(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPEVOLVE_LEDGER_NOISE_GUARD", "1")
    _, run_dir = _behaviourally_identical(tmp_path)
    imp = harness._candidate_task_impact(run_dir, "cand_0001", "val")
    assert imp["causal"] is False and imp["broke"] == [] and imp["fixed"] == []
    assert imp["n_flips"] >= 1 and 8 < imp["null_flips"] < 16           # ~2p(1-p)*49
    out = tmp_path / "w"
    out.mkdir()
    harness._build_ledger(out, run_dir)
    text = (out / "LEDGER.md").read_text()
    assert "no causal claim" in text and "expected from re-measurement noise" in text


def test_guard_pools_every_draw_of_a_capability_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPEVOLVE_LEDGER_NOISE_GUARD", "1")
    adapter, run_dir = make_run(tmp_path, n_train=0, n_val=20, default=0.5)
    seed = run_dir.candidate_dir("seed")
    for tag in ("seed", "seed_b", "seed_c"):            # 3 draws of the SAME bytes under 3 tags
        harness.evaluate_candidate(adapter, seed, run_dir=run_dir, split="val", tag=tag)
    run_dir.snapshot("cand_0001", seed)
    for tag in ("cand_0001", "cand_0001_b", "cand_0001_c"):
        harness.evaluate_candidate(adapter, run_dir.candidate_dir("cand_0001"), run_dir=run_dir,
                                   split="val", tag=tag)
    imp = harness._candidate_task_impact(run_dir, "cand_0001", "val")
    assert imp["parent"] == "seed" and "causal" not in imp            # n=3 pooled draws: claims allowed again


def test_identical_bytes_pool_to_the_same_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPEVOLVE_LEDGER_NOISE_GUARD", "1")
    _, run_dir = _two_identical_draws(tmp_path)
    imp = harness._candidate_task_impact(run_dir, "cand_0001", "val")
    assert imp["n_flips"] == 0          # same capability hash: the two draws are ONE sample of 2 trials
