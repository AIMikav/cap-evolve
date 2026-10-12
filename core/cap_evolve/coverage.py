"""Coverage of a paired comparison when some cells are missing (pure, stdlib).

v1 vetoes a candidate whose joint coverage is below ``min_coverage`` (0.95 in the E10 specs). On
SpreadsheetBench hill-climb seed 2 the incumbent itself had three tasks with ``valid_trials=0``, never
retried, so every candidate sat at 37/40 = 92.5% forever and 11 of 12 screen-passing candidates were
vetoed. The measurement problem is the incumbent's, not the candidate's.

Missingness is classified by who it falls on:
  * incumbent-side, persistent: retry, then QUARANTINE the task (drop it from the evaluable set E);
  * candidate-side, exogenous: drop the pair, which costs power only;
  * candidate-side in excess of the incumbent's (differential): the candidate probably broke those
    tasks, count the excess as failures;
  * anything still missing between the floor and 1.0: tipping-point check (impute the missing deltas
    at ``impute`` and see whether the screen still passes).

Design: gate_budget_design.md section 4.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping


def valid_map(per_task) -> dict:
    """``{task_id: reward}`` over tasks with at least one valid trial."""
    out = {}
    for pt in per_task or []:
        raw = pt.get("raw") or {}
        valid = raw.get("valid_trials")
        if valid is None:
            valid = 0 if raw.get("errored") and not pt.get("trial_rewards") else 1
        if valid:
            out[str(pt.get("task_id"))] = float(pt.get("reward") or 0.0)
    return out


def missing_ids(per_task, planned: Iterable[str]) -> list:
    """Planned task ids with no valid trial in ``per_task`` (absent rows count as missing)."""
    have = valid_map(per_task)
    return sorted(str(t) for t in planned if str(t) not in have)


def evaluable_set(planned: Iterable[str], quarantined: Iterable[str]) -> list:
    q = {str(t) for t in quarantined}
    return [str(t) for t in planned if str(t) not in q]


def joint_coverage_on(evaluable: Iterable[str], cur_per_task, cand_per_task) -> float:
    E = {str(t) for t in evaluable}
    if not E:
        return 1.0
    both = set(valid_map(cur_per_task)) & set(valid_map(cand_per_task))
    return len(both & E) / len(E)


def differential_failures(evaluable: Iterable[str], cur_per_task, cand_per_task, *,
                          min_excess: int = 2, frac: float = 0.05) -> list:
    """Tasks to count as candidate FAILURES (reward 0.0) because the candidate is missing on them
    in excess of the incumbent: ``cand_only_missing - inc_only_missing > max(min_excess, frac*|E|)``.
    Returns [] when the excess is within the allowance (the gaps are then treated as exogenous)."""
    E = {str(t) for t in evaluable}
    cur, cand = valid_map(cur_per_task), valid_map(cand_per_task)
    cand_only = sorted(t for t in E if t in cur and t not in cand)
    inc_only = [t for t in E if t in cand and t not in cur]
    if len(cand_only) - len(inc_only) > max(min_excess, frac * len(E)):
        return cand_only
    return []


def paired_deltas_on(evaluable: Iterable[str], cur_per_task, cand_per_task, failures=()) -> list:
    """Per-task cand - cur over E; ``failures`` enter with candidate reward 0.0."""
    E = [str(t) for t in evaluable]
    cur, cand = valid_map(cur_per_task), valid_map(cand_per_task)
    fail = {str(t) for t in failures}
    out = []
    for t in E:
        if t not in cur:
            continue
        if t in cand:
            out.append(cand[t] - cur[t])
        elif t in fail:
            out.append(0.0 - cur[t])
    return out


def _mean_se(d):
    n = len(d)
    if n == 0:
        return 0.0, 0.0
    m = sum(d) / n
    if n < 2:
        return m, 0.0
    return m, math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n)


def tipping_point(deltas, n_missing: int, *, impute: float = -0.5, k_se: float = 1.0) -> dict:
    """Would the screen still pass if every missing pair had regressed by ``impute``?

    ``flips`` is True when the observed deltas pass ``mean > k_se * SE`` but the imputed ones do not,
    i.e. the missing cells could change the verdict (status ``unresolved_missing``: re-run them)."""
    m0, se0 = _mean_se(deltas)
    base_pass = len(deltas) >= 2 and m0 > k_se * se0 and m0 > 0
    if n_missing <= 0:
        return {"base_pass": bool(base_pass), "imputed_pass": bool(base_pass), "flips": False,
                "mean": m0, "imputed_mean": m0, "n_missing": 0}
    full = list(deltas) + [float(impute)] * int(n_missing)
    m1, se1 = _mean_se(full)
    imp_pass = len(full) >= 2 and m1 > k_se * se1 and m1 > 0
    return {"base_pass": bool(base_pass), "imputed_pass": bool(imp_pass),
            "flips": bool(base_pass and not imp_pass), "mean": m0, "imputed_mean": m1,
            "imputed_z": (m1 / se1 if se1 > 0 else math.inf if m1 > 0 else 0.0),
            "n_missing": int(n_missing)}
