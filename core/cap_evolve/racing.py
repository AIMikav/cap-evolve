"""Pure statistics for the P2 (SH-C) evaluator: pooled SE, kill-only halving, confirmation against a
concurrent control, and the single-candidate group-sequential boundary.

Stdlib only; nothing here touches a run directory or an adapter. Design and calibration:
``gate_budget_design.md`` (sections 2-3). Every threshold is an argument; the shipped defaults live in
``promotion.P2Params``.
"""

from __future__ import annotations

import math
import random
from typing import Mapping, Sequence

#: Prior for the within-task Bernoulli variance (E[p(1-p)] of a typical benchmark), shrunk toward by
#: ``within_task_variance`` so a handful of all-equal trials never yields SE = 0.
V_PRIOR = 0.10
V_PRIOR_DF = 4.0


def pooled_se(n: int, task_counts: Sequence[int], v: float, m: int) -> float:
    """SE of a mean of ``n`` candidate rollouts against an incumbent estimated from ``m`` trials/task.

    ``task_counts[t]`` = candidate rollouts on task ``t``. SE^2 = v * (1/n + sum_t j_t^2 / (m n^2)): the
    incumbent's error is shared by every candidate rollout on the same task, so repeated passes over a
    task do not average it away (a naive per-rollout SD understates SE by about 20% at n=147).
    """
    if n <= 0:
        return math.inf
    sj2 = sum(int(j) * int(j) for j in task_counts)
    return math.sqrt(max(0.0, v) * (1.0 / n + sj2 / (max(1, m) * n * n)))


def within_task_variance(trials: Mapping[str, Sequence[float]]) -> float:
    """Pooled unbiased within-task variance of per-rollout rewards, shrunk toward ``V_PRIOR``."""
    ss, df = 0.0, 0
    for rs in trials.values():
        rs = [float(r) for r in rs]
        if len(rs) > 1:
            mu = sum(rs) / len(rs)
            ss += sum((r - mu) ** 2 for r in rs)
            df += len(rs) - 1
    return (ss + V_PRIOR_DF * V_PRIOR) / (df + V_PRIOR_DF)


def halve(stats: Mapping[str, Mapping[str, float]], keep: int) -> list[str]:
    """Kill-only successive-halving step. ``stats[cid] = {"mean": .., "z": ..}``.

    Keep the top ``keep`` candidates by z among those with mean > 0 (at least none: a rung may kill
    everyone). Rungs never accept; ties break on candidate id for determinism.
    """
    live = [(cid, s) for cid, s in stats.items() if float(s.get("mean", 0.0)) > 0.0]
    live.sort(key=lambda kv: (-float(kv[1].get("z", 0.0)), kv[0]))
    return [cid for cid, _ in live[:max(0, int(keep))]]


def confirm_with_control(cand: Mapping[str, Sequence[float]], ctrl: Mapping[str, Sequence[float]], *,
                         z_conf: float = 1.5, dmin: float = 0.02, min_coverage: float = 0.6,
                         n_planned: int | None = None) -> dict:
    """Confirmation of one candidate against a concurrently measured incumbent control.

    ``cand`` / ``ctrl`` = ``{task: [trial rewards]}`` measured in the same window on the same tasks.
    The paired delta of a task is mean(cand) - mean(ctrl); SE is model based (pooled within-task
    variance over both arms, shrunk to ``V_PRIOR``): SE^2 = v * sum_t(1/a_t + 1/b_t) / T^2. Passes iff
    mean >= ``dmin`` and z >= ``z_conf`` and at least ``min_coverage`` of the planned tasks are paired.
    """
    tasks = [t for t in cand if cand[t] and ctrl.get(t)]
    T = len(tasks)
    planned = n_planned or len(set(cand) | set(ctrl)) or 0
    cov = (T / planned) if planned else 1.0
    if T == 0:
        return {"pass": False, "mean": 0.0, "se": math.inf, "z": 0.0, "n": 0, "coverage": 0.0,
                "dmin": dmin, "z_conf": z_conf}
    mean = sum(sum(cand[t]) / len(cand[t]) - sum(ctrl[t]) / len(ctrl[t]) for t in tasks) / T
    v = within_task_variance({**{f"c{t}": cand[t] for t in tasks}, **{f"k{t}": ctrl[t] for t in tasks}})
    se = math.sqrt(v * sum(1.0 / len(cand[t]) + 1.0 / len(ctrl[t]) for t in tasks)) / T
    z = mean / se if se > 0 else (math.inf if mean > 0 else 0.0)
    ok = T >= 2 and mean >= dmin and mean > 0 and z >= z_conf and cov >= min_coverage
    return {"pass": bool(ok), "mean": mean, "se": se, "z": z, "n": T, "coverage": cov,
            "dmin": dmin, "z_conf": z_conf, "min_coverage": min_coverage}


# ---- single candidate: group-sequential ----------------------------------------------------------

GS_LOOKS = (12, 24, 49, 98, 147)
GS_FUTILITY = (-0.5, 0.0, 0.3, 0.7)
GS_C = 2.03


def gs_boundary(n: int, n_max: int, c: float = GS_C) -> float:
    """O'Brien-Fleming-type efficacy boundary on z: ``c * sqrt(n_max / n)``."""
    return c * math.sqrt(n_max / max(1, n))


def gs_decide(look: int, n: int, z: float, *, looks: Sequence[int] = GS_LOOKS, c: float = GS_C,
              futility: Sequence[float] = GS_FUTILITY) -> str:
    """One interim look: ``accept`` | ``kill`` | ``continue``. The last look never continues
    (it either crosses its boundary, ``c``, or the candidate is rejected)."""
    n_max = looks[-1]
    if z >= gs_boundary(n, n_max, c):
        return "accept"
    if look >= len(looks) - 1:
        return "kill"
    if look < len(futility) and z < futility[look]:
        return "kill"
    return "continue"


def draw_order(ids: Sequence[str], n: int, seed) -> list[str]:
    """``n`` task draws as repeated seeded passes over ``ids`` (no task twice before all once)."""
    rng = random.Random(f"{seed}")
    out: list[str] = []
    while len(out) < n:
        p = list(ids)
        rng.shuffle(p)
        out.extend(p[:n - len(out)])
    return out
