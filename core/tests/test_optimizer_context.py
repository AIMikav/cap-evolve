"""The optimizer is handed full trajectories + capability guidance in its workdir,
and the per-iteration INSTRUCTIONS.md is rendered from the template (no leftover
placeholders). The injected read-context is excluded from the candidate snapshot."""

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "core"
EXAMPLE = REPO / "examples" / "toy_calc"
MOCK_RUN = REPO / "skills" / "optimizers" / "run-optimizer" / "scripts" / "run.py"
sys.path.insert(0, str(CORE))
sys.path.insert(0, str(EXAMPLE))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CAPEVOLVE_CORE", str(CORE))
    monkeypatch.setenv("CAPEVOLVE_TOY_DATA", str(EXAMPLE))
    monkeypatch.setenv("CAPEVOLVE_MOCK_SCRIPT", str(EXAMPLE / "mock_script.json"))


def _toy_adapter():
    import importlib.util
    spec = importlib.util.spec_from_file_location("toy_calc_adapter_ctx", EXAMPLE / "adapter.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.Adapter()


def test_focus_instructions_render_from_template():
    """The shipped template renders with every placeholder substituted."""
    from cap_evolve import harness
    from cap_evolve.loop import SplitResult

    cur = SplitResult.from_dict({
        "reward": 0.5, "stderr": 0.1,
        "per_task": [{"task_id": "a", "reward": 0.0, "feedback": "missed step"},
                     {"task_id": "b", "reward": 1.0, "feedback": "ok"}],
    })
    text = harness._focus_instructions(cur, None, "whole train set",
                                       capabilities=["tools"], algorithm="hill-climb",
                                       bench_repo="/tmp/somebench")
    assert "{{" not in text and "}}" not in text          # nothing left unrendered
    assert "./trajectories/" in text                       # read-pointer present
    assert "/tmp/somebench" in text                        # bench repo surfaced
    assert "code" in text.lower()                          # code-bearing-tools guidance


def test_injects_trajectories_and_guidance_then_excludes_from_snapshot(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    from cap_evolve import Budget, RunDir, harness

    adapter = _toy_adapter()
    seed = tmp_path / "seed"
    shutil.copytree(EXAMPLE / "capability", seed)
    run_dir = RunDir.create(tmp_path / ".capevolve", ts="ctx", budget=Budget(max_iterations=2, stall=3))
    harness.ensure_splits(adapter, run_dir, seed=0)
    base = harness.baseline(adapter, seed, run_dir=run_dir)

    optimizer = harness.optimizer_from_command(
        ["python3", str(MOCK_RUN), "--name", "mock", "--workdir", "{workdir}", "--prompt", "{prompt}"])
    harness.hill_climb_loop(
        adapter, run_dir=run_dir, optimizer=optimizer, current_val=base,
        focus="all", max_iterations=2, gate_kwargs={"mode": "significant", "k_se": 1.0},
        algorithm="hill-climb", capabilities=["system-prompt"],
    )

    workdir = run_dir.root / "work" / "cand_0001"
    # the optimizer's working dir got the full trajectories + capability guidance + a
    # rendered prompt with no leftover placeholders
    assert (workdir / "trajectories").is_dir()
    assert any((workdir / "trajectories").iterdir())
    assert (workdir / "guidance" / "system-prompt" / "SKILL.md").exists()
    instr = (workdir / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "{{" not in instr

    # every candidate is snapshotted (accepted AND rejected), but the injected
    # read-context is NOT stored as part of the candidate
    snap = run_dir.candidate_dir("cand_0001")
    assert snap.exists()
    assert not (snap / "trajectories").exists()
    assert not (snap / "guidance").exists()


def test_evaluate_candidate_task_ids_subset_and_no_record(tmp_path):
    """task_ids restricts scoring to a subset; record=False leaves the run's
    authoritative rollouts/spend untouched (the ABLATION self-eval contract)."""
    from cap_evolve import Budget, RunDir, harness

    adapter = _toy_adapter()
    seed = tmp_path / "seed"
    shutil.copytree(EXAMPLE / "capability", seed)
    run_dir = RunDir.create(tmp_path / ".capevolve", ts="sub", budget=Budget(max_iterations=1, stall=3))
    harness.ensure_splits(adapter, run_dir, seed=0)
    base = harness.baseline(adapter, seed, run_dir=run_dir)
    assert len(base.per_task) >= 2                       # toy split has >1 val task

    one = base.per_task[0]["task_id"]
    rollouts_before = sorted((run_dir.rollouts / "val").glob("*.json"))
    spent_before = run_dir.spent.metric_calls

    res = harness.evaluate_candidate(adapter, seed, run_dir=run_dir, split="val",
                                     n_trials=1, tag="ablate", task_ids=[one], record=False)
    # scored exactly the requested subset
    assert [pt["task_id"] for pt in res.per_task] == [one]
    # record=False: no new authoritative rollouts, no metric-call budget burn
    assert sorted((run_dir.rollouts / "val").glob("*.json")) == rollouts_before
    assert run_dir.spent.metric_calls == spent_before
    # scratch rollouts went under the candidate dir instead
    assert (seed / ".ablate_rollouts").is_dir()


def test_ablation_kit_injected_and_excluded_from_snapshot(tmp_path):
    """Each iteration's workdir gets baseline_pertask.json + an executable, valid
    ./ablate wrapper; both are excluded from the candidate snapshot."""
    import ast
    import json
    import os

    if shutil.which("git") is None:
        pytest.skip("git not available")
    from cap_evolve import Budget, RunDir, harness

    adapter = _toy_adapter()
    seed = tmp_path / "seed"
    shutil.copytree(EXAMPLE / "capability", seed)
    run_dir = RunDir.create(tmp_path / ".capevolve", ts="kit", budget=Budget(max_iterations=1, stall=3))
    harness.ensure_splits(adapter, run_dir, seed=0)
    base = harness.baseline(adapter, seed, run_dir=run_dir)

    optimizer = harness.optimizer_from_command(
        ["python3", str(MOCK_RUN), "--name", "mock", "--workdir", "{workdir}", "--prompt", "{prompt}"])
    harness.hill_climb_loop(
        adapter, run_dir=run_dir, optimizer=optimizer, current_val=base,
        focus="all", max_iterations=1, gate_kwargs={"mode": "significant", "k_se": 1.0},
        algorithm="hill-climb", capabilities=["system-prompt"], optimizer_name="mock",
        project_dir=EXAMPLE,
    )

    workdir = run_dir.root / "work" / "cand_0001"
    bp = workdir / "baseline_pertask.json"
    ablate = workdir / "ablate"
    assert bp.exists() and ablate.exists()
    assert os.access(ablate, os.X_OK)                    # executable
    ast.parse(ablate.read_text(encoding="utf-8"))        # valid python
    baseline = json.loads(bp.read_text(encoding="utf-8"))
    assert set(baseline) == {pt["task_id"] for pt in base.per_task}

    snap = run_dir.candidate_dir("cand_0001")
    assert not (snap / "ablate").exists()
    assert not (snap / "baseline_pertask.json").exists()


def test_parallel_note_gated_by_optimizer_capability():
    """{{PARALLEL_NOTE}} fans out subagents only for a parallel-capable optimizer
    (claude-code); a non-parallel one (mock) is told to work sequentially."""
    from cap_evolve import harness

    assert harness._optimizer_parallel("claude-code") is True
    assert harness._optimizer_parallel("mock") is False
    assert harness._optimizer_parallel(None) is False

    on = harness._parallel_note(True, "claude-code")
    off = harness._parallel_note(False, "mock")
    assert "fan out" in on.lower()
    assert "sequential" in off.lower() or "one at a time" in off.lower()
    assert "fan out" not in off.lower()
