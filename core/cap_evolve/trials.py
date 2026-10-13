"""Concurrent multi-trial helper for adapters.

The harness (``harness._run_and_score``) has a fast path: if an adapter exposes
``run_trials(tasks, ctx, *, n_trials, base_seed) -> {task_id: [rollout_t0, ...]}``
it asks for the whole ``task × trial`` grid in one call instead of looping trials
sequentially. This helper builds that return value by running each ``(task, trial)``
rollout through the adapter's existing per-rollout function, concurrently, bounded
by ``max_workers``.

Seed contract (see ``adapter.py``): trial ``k`` runs with ``seed = base_seed + k`` so
distinct trials are independent draws (honest pass^k + significance gate). Scoring is
NOT done here — the harness scores each returned rollout, so this only parallelizes
rollout *generation*.
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
import sysconfig
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .types import Rollout, Task

# ---- candidate-fault attribution ---------------------------------------------------------------
# An exception that escapes ``run_one`` is "an infra error" by default (missing data, retryable). But
# when the traceback passes through a file that lives under a LIVE candidate directory, the candidate's
# own code raised it (an edited tool, a broken helper): that is a measured failure of the candidate, and
# dropping it would hide a regression (a candidate that crashes on 10% of tasks the incumbent passes
# hides ~0.056 of regression under ``drop``). The harness registers the directories it is evaluating;
# the attribution is stored as ``Rollout.metadata["error_kind"] = "candidate"``, which
# ``errored_trial_policy: fail`` scores as 0.0. Anything else (adapter code, ConnectionError, a runner
# crash) stays unattributed and therefore missing.
_ROOTS: dict[str, int] = {}
_ROOTS_LOCK = threading.Lock()


@contextlib.contextmanager
def candidate_scope(*roots):
    """Mark ``roots`` (candidate directories under evaluation) as candidate-owned code for the
    duration. Re-entrant and safe for several candidates evaluating in parallel threads."""
    keys = []
    for r in roots:
        if r is None:
            continue
        try:
            keys.append(os.path.realpath(os.fspath(r)))
        except TypeError:
            continue
    with _ROOTS_LOCK:
        for k in keys:
            _ROOTS[k] = _ROOTS.get(k, 0) + 1
    try:
        yield
    finally:
        with _ROOTS_LOCK:
            for k in keys:
                _ROOTS[k] -= 1
                if _ROOTS[k] <= 0:
                    del _ROOTS[k]


_STDLIB = os.path.realpath(sysconfig.get_paths()["stdlib"]) + os.sep
_TRANSIENT = re.compile(r"\b429\b|rate.?limit|too many requests|timed? ?out|connection (?:reset|refused)",
                        re.IGNORECASE)


def _attributable_root(root: str) -> bool:
    """A directory that may be treated as "the candidate's code": never a project root or a directory
    holding adapter files (an adapter bug there would be blamed on the candidate)."""
    for marker in ("adapter.py", "adapters", "capevolve.yaml", "pyproject.toml", ".git"):
        if os.path.exists(os.path.join(root, marker)):
            return False
    return True


def raised_in_candidate_code(exc: BaseException) -> bool:
    """Did the CANDIDATE's own code raise ``exc``?

    True only when the innermost non-stdlib frame of the traceback is a file under a live candidate dir
    (so a third-party library failing inside a call the candidate made, an adapter bug, an HTTP 429, a
    timeout or a connection reset is never blamed on the candidate). Only ``__cause__`` is followed,
    never ``__context__`` (an unrelated exception being handled when the real one was raised)."""
    with _ROOTS_LOCK:
        roots = tuple(k.rstrip(os.sep) + os.sep for k in _ROOTS if _attributable_root(k))
    if not roots:
        return False
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if not _TRANSIENT.search(str(exc) or ""):
            inner = None
            tb = exc.__traceback__
            while tb is not None:
                fn = os.path.realpath(tb.tb_frame.f_code.co_filename or "")
                if fn and not fn.startswith(_STDLIB):
                    inner = fn
                tb = tb.tb_next
            if inner and inner.startswith(roots):
                return True
        exc = exc.__cause__
    return False


def run_trials_pool(
    run_one: Callable[[Task, int], Rollout],
    tasks: list[Task],
    *,
    n_trials: int,
    base_seed: int,
    max_workers: int = 1,
    on_progress: Callable[[], None] | None = None,
) -> dict[str, list[Rollout]]:
    """Run the ``task × trial`` grid concurrently and return trial-ordered rollouts.

    ``run_one(task, seed) -> Rollout`` produces ONE rollout. Returns
    ``{task_id: [rollout_t0, ..., rollout_t{n-1}]}`` (length ``n_trials`` per task,
    trial order preserved). An exception in ``run_one`` becomes an error ``Rollout``
    for that ``(task, trial)`` so one bad trial can't sink the batch. ``max_workers``
    bounds concurrency; ``1`` runs sequentially (identical result, no threads).

    ``on_progress`` (optional), if given, is called once per completed ``(task,
    trial)`` job — in this function's own thread, never a worker thread, so it needs
    no locking. This is the harness's hook for a heartbeat during a long eval (see
    #589): without it, generating hundreds of rollouts through this pool is
    completely silent from the first job to the last.
    """
    n_trials = max(0, int(n_trials))
    max_workers = max(1, int(max_workers))
    results: dict[str, list[Rollout]] = {t.id: [None] * n_trials for t in tasks}  # type: ignore[list-item]
    jobs = [(t, k) for t in tasks for k in range(n_trials)]

    def _one(job):
        task, k = job
        try:
            return task.id, k, run_one(task, base_seed + k)
        except Exception as e:  # infra error, not a scored failure ... unless the candidate's code raised it
            meta = {"error_kind": "candidate"} if raised_in_candidate_code(e) else {}
            return task.id, k, Rollout(task_id=task.id, error=f"trial {k} raised: {e}", metadata=meta)

    if not jobs:
        return results
    # Adapters keep the stdout JSON-contract clean by redirecting their runner's stdout to
    # stderr (e.g. tau2's run_batch). contextlib.redirect_stdout swaps the PROCESS-GLOBAL
    # sys.stdout and is NOT thread-safe: with concurrent per-call redirects, one thread's
    # exit can restore real stdout while another is still printing, leaking runner output
    # into the pure-JSON stdout. Wrap the whole pool once so real stdout is never the
    # "current" target during the threads — inner per-call redirects then only ever
    # save/restore sys.stderr, so nothing can leak.
    with contextlib.redirect_stdout(sys.stderr):
        if max_workers == 1:
            for job in jobs:
                tid, k, rollout = _one(job)
                results[tid][k] = rollout
                if on_progress is not None:
                    on_progress()
        else:
            # as_completed (not ex.map) so ``on_progress`` fires as each job ACTUALLY
            # finishes, not merely in submission order — a slow first job must not
            # block the progress signal for jobs that finished behind it.
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = [ex.submit(_one, job) for job in jobs]
                for fut in as_completed(futures):
                    tid, k, rollout = fut.result()
                    results[tid][k] = rollout
                    if on_progress is not None:
                        on_progress()
    return results
