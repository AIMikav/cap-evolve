"""Shared fakes for the P2 / gate-and-budget tests: a seeded Bernoulli adapter and a run-dir builder.

No model calls: a rollout's outcome is a deterministic function of (task, seed, cfg.json in the
candidate dir), so a screening fluke does not repeat on fresh seeds.
"""

import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "core"))


class BernoulliAdapter:
    """``cfg.json``: ``{"default": p, "p": {task: p}, "salt": n, "err": [task...]}``."""

    def __init__(self, task_ids):
        self.task_ids = list(task_ids)
        self.calls = []

    def tasks(self, split):
        from cap_evolve import Task
        return [Task(id=t) for t in self.task_ids]

    def run_target(self, task, ctx, *, seed=0):
        from cap_evolve import Rollout
        cfg = json.loads((Path(ctx) / "cfg.json").read_text())
        self.calls.append((task.id, seed))
        if task.id in cfg.get("err", []):
            return Rollout(task_id=task.id, error="boom")
        p = cfg.get("p", {}).get(task.id, cfg.get("default", 0.5))
        u = random.Random(f"{task.id}:{seed}:{cfg.get('salt', 0)}").random()
        return Rollout(task_id=task.id, output="pass" if u < p else "fail", trace="x", cost_usd=0.01)

    def score(self, task, rollout):
        from cap_evolve import Score
        r = 1.0 if rollout.output == "pass" else 0.0
        return Score(task_id=task.id, reward=r, trial_rewards=[r])

    def apply(self, candidate_dir, edits=None):
        return None


def make_run(tmp_path, *, n_train=24, n_val=24, n_test=0, default=0.5, max_metric_calls=0, ts="p2",
             spec=None):
    """A run dir with frozen splits and a snapshotted+best ``seed`` candidate. Returns (adapter, run_dir)."""
    from cap_evolve import RunDir
    from cap_evolve.rundir import Budget
    from cap_evolve.splits import Splits
    ids = [f"t{i:03d}" for i in range(n_train + n_val + n_test)]
    seed = tmp_path / "seed"
    seed.mkdir(exist_ok=True)
    (seed / "cfg.json").write_text(json.dumps({"default": default, "p": {}}))
    run_dir = RunDir.create(tmp_path / ".capevolve", ts=ts,
                            budget=Budget(max_iterations=50, max_metric_calls=max_metric_calls))
    run_dir.write_splits(Splits(train=ids[:n_train], val=ids[n_train:n_train + n_val],
                                test=ids[n_train + n_val:], seed=7))
    run_dir.snapshot("seed", seed)
    run_dir.set_best("seed")
    if spec is not None:
        spec_file = tmp_path / "capevolve.yaml"
        spec_file.write_text("".join(f"{k}: {json.dumps(v).lower() if isinstance(v, bool) else v}\n"
                                     for k, v in spec.items()))
        run_dir.log_event("run_config", spec=str(spec_file))
    return BernoulliAdapter(ids), run_dir


def make_candidate(tmp_path, name, cfg):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "cfg.json").write_text(json.dumps(cfg))
    return d
