"""The P2 evaluator (policy "SH-C"): ONE scheduler shared by hill-climb rounds and the agent-optimize
conductor.

A *round* takes K candidates built from one incumbent and returns at most one champion:

  1. rung 1   kill-only, ``rung_sizes[0]`` rollouts per candidate on a seeded random subset of the
              feedback stratum F (same subset for every candidate and for the incumbent's F draws)
  2. rung 2   survivors only, up to ``rung_sizes[1]`` cumulative rollouts, ranked on pooled F draws
  3. control  once per round, if a survivor exists: the incumbent re-measured on the confirm stratum C
              (``ctrl_m`` trials per task), concurrently with the confirmations
  4. confirm  at most ``max_survivors`` survivors, ``conf_n`` FRESH rollouts each on C, none of the rung
              data reused; accept the best paired mean among those with z >= ``z_conf`` and mean >= dmin

Rungs never accept. A confirmation that does not fit the budget returns ``unconfirmed`` (the candidate
stays pending); it is never a reject and never an accept. With a single pending candidate
(``Round.sequential``) a group-sequential test on C replaces rungs + confirmation.

Information barrier. F is what the proposer may see (traces, per-task rewards). C is used only here;
every value this module returns about C is an aggregate (mean, z, n) or a verdict bit, never a task id,
a trace, or a per-task score.

The unit of work is a ``Job`` (at most ``chunk`` rollouts of one candidate on one stage). Jobs are plain
data: a caller may run them inline (``Round.run``), on a thread pool, or in the conductor's shared slot
pool, in any order and across processes, because ``Round.collect`` rebuilds every statistic from the
rollouts on disk. Every job reserves its rollouts before dispatch (``RunDir.reserve_metric_calls``).

Public surface (stable; PR-2 AO conductor and PR-4 HC-2 build on exactly this):

    ev = P2Evaluator(adapter, run_dir, params)
    ev.feedback_ids() / ev.confirm_ids()        split accessors (C ids are for the evaluator only)
    ev.plan_round(k_wanted, fits=...)           -> RoundPlan (K shrunk until the worst case fits)
    rnd = ev.open_round(candidates, incumbent)  -> Round, persisted, resumable
    rnd.rung_jobs(1) / rnd.run_job(job) / rnd.close_rung(1)  -> {cid: RungVerdict}
    rnd.run_rung(1, map_fn=...)                 jobs + close in one call (map_fn = parallel executor)
    rnd.confirm_jobs() / rnd.close_confirm()    -> {cid: ConfirmVerdict}, plus Round.champion
    rnd.sequential(cid)                         K = 1 group-sequential on C
    ev.final_estimate(champion_dir, seed_dir)   champion vs seed on C, fresh seeds
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

#: Verdict statuses. ``kill``/``advance`` come from rungs, ``accept``/``reject`` from a confirmation,
#: ``unconfirmed`` = survived but the budget could not fund its confirmation (pending, not a reject),
#: ``unfunded`` = a rung could not be funded (the round ends).
STATUSES = ("kill", "advance", "accept", "reject", "unconfirmed", "unfunded")

#: Stage names a Job can carry.
STAGES = ("rung1", "rung2", "control", "confirm", "seq")


@dataclass(frozen=True)
class Job:
    """A bounded unit of evaluation: <= ``chunk`` rollouts of ONE candidate on ONE stage."""
    job_id: str                 # stable: f"{round_id}:{cid}:{stage}:{index}"
    round_id: str
    cid: str                    # candidate id; the incumbent's control job uses the incumbent's id
    stage: str                  # one of STAGES
    role: str                   # "feedback" (F) | "confirm" (C)
    tag: str                    # rollout tag the job writes under (disjoint per job)
    ids: tuple                  # task ids of this chunk (never shown to a proposer when role == "confirm")
    n_trials: int
    trial_offset: int           # fresh-seed range; confirm ranges never overlap rung ranges
    rollouts: int               # len(ids) * n_trials, the amount reserved against max_metric_calls

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RungVerdict:
    cid: str
    status: str                 # kill | advance | unfunded
    mean: float                 # paired mean vs the incumbent's pooled F draws
    z: float
    n: int                      # candidate rollouts counted so far
    reason: str = ""


@dataclass
class ConfirmVerdict:
    cid: str
    status: str                 # accept | reject | unconfirmed
    mean: float | None = None   # aggregates on C only; None when unconfirmed
    z: float | None = None
    n: int = 0
    reason: str = ""

    def bit(self) -> str:
        """What the proposer is told about C: ``accept`` | ``reject`` | ``pending``. Nothing else."""
        return {"accept": "accept", "reject": "reject"}.get(self.status, "pending")


@dataclass
class RoundPlan:
    k: int                      # candidates the round can afford (<= k_wanted)
    worst_case: int             # rollouts: rung 1 + rung 2 + control + survivors * conf_n
    funded: bool                # False: even K=1 does not fit; run no round


class P2Evaluator:
    """Owns the F/C split, the incumbent's pooled F draws, and budget reservations for P2 rounds."""

    def __init__(self, adapter, run_dir, params, *, workers: int | None = None,
                 errored_policy: str | None = None):
        """``params`` = ``promotion.P2Params``. Requires ``split_mode: pooled`` splits (``Splits.pooled``)."""
        raise NotImplementedError

    # -- split accessors ------------------------------------------------------------------------
    def feedback_ids(self) -> list:
        """Task ids of F: what the proposer and the rungs may use."""
        raise NotImplementedError

    def confirm_ids(self) -> list:
        """Task ids of C. For the evaluator only: never write them where a proposer can read."""
        raise NotImplementedError

    @property
    def proposer_split(self) -> str:
        """The split name to hand ``OptimizerContext.inject`` / trajectory writers: ``"feedback"``."""
        return "feedback"

    # -- budget -----------------------------------------------------------------------------------
    def reserve(self, n: int, label: str):
        """Context manager around ``RunDir.reserve_metric_calls``; raises ``BudgetExhausted``."""
        raise NotImplementedError

    def plan_round(self, k_wanted: int) -> RoundPlan:
        """Shrink K until the worst-case cost fits the remaining ``max_metric_calls``."""
        raise NotImplementedError

    # -- rounds -----------------------------------------------------------------------------------
    def open_round(self, candidates: Mapping[str, Path], incumbent: tuple) -> "Round":
        """``candidates = {cid: workdir}``, ``incumbent = (cid, dir)``. Persists the round."""
        raise NotImplementedError

    def final_estimate(self, champion_dir: Path, seed_dir: Path, *, n_trials: int = 4) -> dict:
        """Champion vs seed on C with fresh seeds: ``{mean, se, n}``; the unbiased internal estimate."""
        raise NotImplementedError


class Round:
    """State of one round; persisted under the run dir so a conductor can crash and resume."""

    round_id: str
    champion: str | None        # best accepted confirmation; None until close_confirm()

    def rung_jobs(self, rung: int) -> list:
        """Chunked ``Job``s for rung 1 or 2 over the live candidates (rung 2: survivors only)."""
        raise NotImplementedError

    def run_job(self, job: Job):
        """Execute one job: reserve -> ``evaluate_candidate`` -> convert reservation. Idempotent."""
        raise NotImplementedError

    def close_rung(self, rung: int) -> dict:
        """``{cid: RungVerdict}``; kill-only halving on the pooled F draws. Never accepts."""
        raise NotImplementedError

    def run_rung(self, rung: int, map_fn: Callable | None = None) -> dict:
        """``rung_jobs`` + ``run_job`` over ``map_fn(fn, jobs)`` (a parallel executor) + ``close_rung``."""
        raise NotImplementedError

    def confirm_jobs(self) -> list:
        """Control jobs (incumbent on C, ``ctrl_m`` trials) and fresh confirmation jobs per survivor."""
        raise NotImplementedError

    def close_confirm(self) -> dict:
        """``{cid: ConfirmVerdict}``; sets ``champion``. A confirmation that did not fit is
        ``unconfirmed``, never a reject."""
        raise NotImplementedError

    def sequential(self, cid: str, map_fn: Callable | None = None) -> ConfirmVerdict:
        """K = 1: group-sequential test on C with looks ``gs_looks`` and a concurrent control."""
        raise NotImplementedError
