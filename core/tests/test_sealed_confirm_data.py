"""Pooled mode: every artifact derived from the hidden confirm stratum C lives OUTSIDE every tree a
proposer can read. Both optimizers can read the project and the run dir: the agent-optimize conductor's
cwd is the common parent of the run dir and the project, and a hill-climb proposer runs in
``<run>/work/<cid>``. The tests walk those roots after a full pooled run and assert that no C task id
appears in any file NAME or file CONTENT."""

import json
import os
import re
import tempfile
from pathlib import Path

from p2_fakes import make_run

from cap_evolve import harness, promotion  # noqa: E402
from cap_evolve import evaluator  # noqa: E402

PARAMS = promotion.P2Params.from_spec({"promotion_rule": "P2", "p2_conf_n": 96})


def _opt(cfg):
    f = Path(tempfile.mkdtemp()) / "c.json"
    f.write_text(json.dumps(cfg))
    code = ("import sys,pathlib; w=pathlib.Path(sys.argv[1]); "
            "(w/'cfg.json').write_text(pathlib.Path(sys.argv[2]).read_text())")
    return harness.optimizer_from_command(["python3", "-c", code, "{workdir}", str(f)])


def _leaks(root: Path, ids) -> list:
    pats = [(t, re.compile(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![0-9])")) for t in ids]
    bad = []
    for dirpath, dirnames, files in os.walk(root, followlinks=True):
        for name in list(dirnames) + files:
            for t, pat in pats:
                if pat.search(name):
                    bad.append((os.path.join(dirpath, name), "name", t))
        for name in files:
            p = os.path.join(dirpath, name)
            try:
                text = Path(p).read_text(errors="ignore")
            except OSError:
                continue
            for t, pat in pats:
                if pat.search(text):
                    bad.append((p, "content", t))
                    break
    return bad


def _full_run(tmp_path):
    adapter, run_dir = make_run(tmp_path, n_train=24, n_val=24, default=0.4)
    base = harness.baseline_pooled(adapter, tmp_path / "seed", run_dir=run_dir, n_trials=4)
    res = harness.hill_climb_loop(adapter, run_dir=run_dir, optimizer=_opt({"default": 0.9, "p": {}}),
                                  current_val=base, max_iterations=2, n_trials=1,
                                  gate_kwargs={"promotion_rule": "P2", "p2_conf_n": 96})
    # a K=2 round as well (control + confirmation jobs, round state, private log)
    ev = evaluator.P2Evaluator(adapter, run_dir, PARAMS)
    cands = {}
    for i in range(2):
        d = tmp_path / f"k{i}"
        d.mkdir()
        (d / "cfg.json").write_text(json.dumps({"default": 0.8, "p": {}, "salt": i + 9}))
        cands[f"k{i}"] = d
    rnd = ev.open_round(cands, ("seed", run_dir.candidate_dir("seed")))
    rnd.run_rung(1)
    rnd.run_rung(2)
    rnd.confirm()
    ev.final_estimate(cands["k0"], run_dir.candidate_dir("seed"))
    return adapter, run_dir, res


def test_no_confirm_id_is_readable_from_the_project_the_run_dir_or_a_hill_climb_workdir(tmp_path):
    adapter, run_dir, res = _full_run(tmp_path)
    C = run_dir.read_splits().confirm
    assert C and run_dir.sealed_dir is not None
    project = run_dir.root.parent.parent                 # the agent-optimize conductor's cwd
    assert project == tmp_path
    assert not _leaks(project, C)
    for work in (run_dir.root / "work").iterdir():       # every hill-climb proposer cwd
        assert not _leaks(work, C)
    assert not _leaks(run_dir.root, C)


def test_the_sealed_dir_holds_c_and_is_outside_every_proposer_root(tmp_path):
    adapter, run_dir, _ = _full_run(tmp_path)
    sealed = run_dir.sealed_dir
    C = run_dir.read_splits().confirm
    proposer_roots = [run_dir.root, run_dir.root.parent.parent]
    assert all(r not in sealed.parents and sealed != r for r in proposer_roots)
    assert not str(sealed).startswith(str(run_dir.root.parent.parent) + os.sep)
    assert _leaks(sealed, C)                                              # C is there ...
    assert list((sealed / "rollouts" / "confirm").glob("*.json"))
    assert (sealed / "splits_full.json").exists() and (sealed / "private" / "p2_confirm.jsonl").exists()
    assert list((sealed / "private" / "p2").glob("r*.json"))
    assert not (run_dir.root / "rollouts" / "confirm").exists()
    assert not (run_dir.root / "private").exists() and not (run_dir.root / "p2").exists()
    assert (sealed / "eval_index.jsonl").exists()
    assert all(json.loads(l)["split"] != "confirm" for l in (run_dir.root / "eval_index.jsonl").read_text().splitlines())


def test_splits_json_no_longer_lists_train_val_or_confirm_but_the_framework_still_reads_them(tmp_path):
    adapter, run_dir = make_run(tmp_path, n_train=10, n_val=10)
    harness.ensure_pooled(run_dir)
    on_disk = json.loads(run_dir.splits_path.read_text())
    assert on_disk["train"] == [] and on_disk["val"] == [] and on_disk["confirm"] == [] and on_disk["sealed"]
    sp = run_dir.read_splits()
    assert len(sp.confirm) > 0 and set(sp.feedback) | set(sp.confirm) == set(sp.train) | set(sp.val)
    sp.test_used = False
    run_dir.write_splits(sp)                            # a rewrite (e.g. the test seal) keeps it sealed
    assert json.loads(run_dir.splits_path.read_text())["confirm"] == []


def test_the_sealed_location_is_configurable_and_persisted(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere"
    monkeypatch.setenv("CAPEVOLVE_SEALED_DIR", str(target))
    adapter, run_dir = make_run(tmp_path / "proj", n_train=6, n_val=6) if (tmp_path / "proj").mkdir() is None else (None, None)
    harness.ensure_pooled(run_dir)
    assert run_dir.sealed_dir == target / run_dir.root.name
    monkeypatch.delenv("CAPEVOLVE_SEALED_DIR")
    from cap_evolve import RunDir
    assert RunDir.open(run_dir.root).sealed_dir == target / run_dir.root.name        # found again on resume


def test_legacy_runs_write_nothing_to_a_sealed_dir(tmp_path):
    adapter, run_dir = make_run(tmp_path, n_train=6, n_val=6)
    harness.evaluate_candidate(adapter, run_dir.candidate_dir("seed"), run_dir=run_dir, split="val", tag="seed")
    assert run_dir.sealed_dir is None and not tmp_path.with_name(tmp_path.name + ".sealed").exists()
