"""HC-2: K parallel proposals per round, evaluated by the shared P2 evaluator.

Hill-climb with rule P judges ONE candidate per iteration. HC-2 is the K-round special case of the
same scheduler the agent-optimize conductor uses (``evaluator.P2Evaluator``): one incumbent, K
candidates from K distinct failure clusters, kill-only rungs on the feedback stratum F, a fresh
confirmation of the survivors on the hidden stratum C, at most one champion per round. There is no
second evaluation code path here; this module only builds the proposer's context and runs the
propose -> evaluate -> record loop around ``P2Evaluator``.

Everything is opt-in (``hc2: true`` in the spec, forwarded as ``--hc2``). Without it ``hill_climb_loop``
and rule P run exactly as before.

Proposer context (all benchmark-agnostic):

* fresh failures from F only. Each round takes a rotating minibatch of the incumbent's failing F tasks
  and splits it into clusters; proposal j gets cluster j's traces. A trace for a task in C is never
  copied, whatever directory it sits in (``scrub_confirm_traces`` enforces it after every write);
* contrastive pairs: for a task the incumbent both passed and failed across its pooled draws, one
  passing and one failing trace side by side;
* a diversity gate: a proposal whose diff overlaps one of the last K rejected diffs (or an earlier
  proposal of the same round) above a threshold is re-proposed once with a "different cluster" note,
  then dropped at no rollout cost;
* an optional consolidation mode: a cross-trace summary of what recurs across the cluster's failures,
  and instructions to write per-trace lessons and merge the recurring ones into a single edit.
"""

from __future__ import annotations

import difflib
import json
import math
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .evaluator import thread_map


@dataclass(frozen=True)
class HC2Params:
    """Spec keys are ``hc2_<field>``; ``hc2: true`` turns HC-2 on."""
    k: int = 4                       # proposals per round (shrunk by ``plan_round`` to what fits)
    minibatch: int = 12              # failing F tasks the clusters are drawn from, per round
    per_cluster: int = 4             # failing traces shown per proposal
    pairs: int = 3                   # contrastive pass/fail pairs shown per proposal
    cluster_sim: float = 0.3         # token-Jaccard above which two failures share a cluster
    diversity_threshold: float = 0.6  # diff overlap at/above which a proposal is a near-duplicate
    diversity_window: int = 0        # last-N rejected diffs compared against (0 = K)
    retries: int = 1                 # re-proposals for a near-duplicate before it is dropped
    consolidate: bool = False        # cross-trace consolidation mode
    max_rounds: int = 0              # 0 = the run's max_iterations
    final_estimate_trials: int = 4   # trials per arm of the closing champion-vs-seed estimate on C;
                                     # 0 = no reservation and no estimate (for budgets that cannot afford it)

    @classmethod
    def from_spec(cls, spec: dict | None):
        """``None`` unless ``hc2: true``; else validated params. Requires rule P2 on pooled splits."""
        spec = spec or {}
        if not spec.get("hc2"):
            return None
        if spec.get("promotion_rule") != "P2" or spec.get("split_mode") != "pooled":
            raise ValueError("hc2: true requires promotion_rule: P2 and split_mode: pooled "
                             "(HC-2 runs on the shared P2 evaluator and its F/C partition)")
        d = cls()
        out = cls(
            k=int(spec.get("hc2_k", spec.get("p2_k", d.k))),
            minibatch=int(spec.get("hc2_minibatch", d.minibatch)),
            per_cluster=int(spec.get("hc2_per_cluster", d.per_cluster)),
            pairs=int(spec.get("hc2_pairs", d.pairs)),
            cluster_sim=float(spec.get("hc2_cluster_sim", d.cluster_sim)),
            diversity_threshold=float(spec.get("hc2_diversity_threshold", d.diversity_threshold)),
            diversity_window=int(spec.get("hc2_diversity_window", d.diversity_window)),
            retries=int(spec.get("hc2_retries", d.retries)),
            consolidate=bool(spec.get("hc2_consolidate", d.consolidate)),
            max_rounds=int(spec.get("hc2_max_rounds", d.max_rounds)),
            final_estimate_trials=int(spec.get("hc2_final_estimate_trials", d.final_estimate_trials)))
        if min(out.k, out.minibatch, out.per_cluster) < 1 or out.pairs < 0 or out.retries < 0 \
            or out.final_estimate_trials < 0:
            raise ValueError("hc2_k, hc2_minibatch and hc2_per_cluster must be >= 1; "
                             "hc2_pairs, hc2_retries and hc2_final_estimate_trials >= 0")
        if not 0.0 < out.diversity_threshold <= 1.0:
            raise ValueError("hc2_diversity_threshold must be in (0, 1]")
        return out


# ---- reading F traces ---------------------------------------------------------------------------

@dataclass
class Trace:
    task_id: str
    tag: str
    trial: int
    reward: float
    path: Path
    signature: str = ""


_TOKEN = re.compile(r"[a-z0-9_]{3,}")


def tokens(text: str) -> frozenset:
    return frozenset(_TOKEN.findall((text or "").lower()))


def jaccard(a, b) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _parse_name(stem: str, task_ids) -> tuple | None:
    """``<task>__<tag>__t<k>`` -> (task, tag, k) for a task in ``task_ids``; None otherwise."""
    head, sep, k = stem.rpartition("__t")
    if not sep or not k.isdigit():
        return None
    pos = head.find("__")
    while pos != -1:
        if head[:pos] in task_ids:
            return head[:pos], head[pos + 2:], int(k)
        pos = head.find("__", pos + 2)
    return None


def _signature(rec: dict) -> str:
    sc, ro = rec.get("score") or {}, rec.get("rollout") or {}
    out = ro.get("output")
    return " ".join(str(x) for x in (sc.get("feedback"), ro.get("error"),
                                     (out if isinstance(out, str) else "")[-400:]) if x)


def read_traces(run_dir, split_dirs, feedback_ids, tag_ok) -> list:
    """Every rollout in ``split_dirs`` whose task is in F and whose tag satisfies ``tag_ok``.

    Filtering by F ids here is what keeps a C trace out, even if an evaluator wrote C rollouts into one
    of the directories scanned.
    """
    ids = set(map(str, feedback_ids))
    out = []
    for sd in split_dirs:
        d = Path(run_dir.rollouts) / sd
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            parsed = _parse_name(f.stem, ids)
            if not parsed or not tag_ok(parsed[1]):
                continue
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            reward = float((rec.get("score") or {}).get("reward", 0.0) or 0.0)
            out.append(Trace(parsed[0], parsed[1], parsed[2], reward, f, _signature(rec)))
    return out


def incumbent_tag_ok(cid: str):
    """The incumbent's draws: its own tag, or a round tag (``<round>__<cid>__...``) the evaluator wrote."""
    return lambda tag: cid in tag.split("__")


# ---- clusters, pairs ----------------------------------------------------------------------------

def failing_tasks(traces) -> dict:
    """{task_id: [failing traces]} for tasks with at least one failing draw (reward < 1)."""
    out: dict = {}
    for t in traces:
        if t.reward < 1.0:
            out.setdefault(t.task_id, []).append(t)
    return out


def rotating_minibatch(task_ids, size: int, round_index: int) -> list:
    """A deterministic rotation: sorted ids, window of ``size`` starting at ``round_index * size``."""
    ids = sorted(task_ids)
    if not ids:
        return []
    start = (round_index * size) % len(ids)
    return (ids[start:] + ids[:start])[:size]


def cluster_failures(failed: dict, task_ids, sim: float) -> list:
    """Greedy leader clustering of failing tasks by token overlap of their failure text.

    Returns clusters (lists of task ids) largest first. Tasks with no text of their own each fall into
    a singleton, so a benchmark with opaque feedback still yields distinct clusters.
    """
    toks = {t: frozenset().union(*(tokens(tr.signature) for tr in failed[t])) for t in task_ids}
    clusters: list = []     # [union_tokens, [ids]]
    for t in task_ids:
        best, best_s = None, 0.0
        for c in clusters:
            s = jaccard(toks[t], c[0])
            if s > best_s:
                best, best_s = c, s
        if best is not None and best_s >= sim:
            best[0] = best[0] | toks[t]
            best[1].append(t)
        else:
            clusters.append([toks[t], [t]])
    return [c[1] for c in sorted(clusters, key=lambda c: -len(c[1]))]


def split_to_k(clusters: list, k: int) -> list:
    """Top ``k`` clusters; if there are fewer, halve the largest (interleaved) so each proposal still
    gets a different slice of the failures."""
    clusters = [list(c) for c in clusters]
    while len(clusters) < k:
        clusters.sort(key=len, reverse=True)
        if not clusters or len(clusters[0]) < 2:
            break
        big = clusters.pop(0)
        clusters += [big[0::2], big[1::2]]
    return sorted(clusters, key=len, reverse=True)[:k]


def contrastive_pairs(traces, task_ids, limit: int) -> list:
    """(pass_trace, fail_trace) for tasks the incumbent both passed and failed across its draws."""
    by: dict = {}
    for t in traces:
        if t.task_id in task_ids:
            by.setdefault(t.task_id, []).append(t)
    pairs = []
    for tid in sorted(by):
        good = [t for t in by[tid] if t.reward >= 1.0]
        bad = [t for t in by[tid] if t.reward < 1.0]
        if good and bad:
            pairs.append((good[0], bad[0]))
    return pairs[:limit]


# ---- writing the proposer's view ----------------------------------------------------------------

def scrub_confirm_traces(workdir: Path, confirm_ids) -> int:
    """Delete anything under ``workdir/trajectories`` and ``workdir/contrast`` for a C task id."""
    ids = set(map(str, confirm_ids))
    removed = 0
    for sub in ("trajectories", "contrast"):
        root = workdir / sub
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.is_file() and any(f.name.startswith(i + "__") for i in ids):
                f.unlink()
                removed += 1
    return removed


def write_cluster_context(workdir: Path, *, cluster, failed: dict, traces, params: HC2Params,
                          confirm_ids) -> dict:
    """Replace ``workdir/trajectories`` with this cluster's F failures; add ``contrast/`` pairs."""
    dst = workdir / "trajectories"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    shown = []
    for tid in cluster[:params.per_cluster]:
        tr = failed[tid][0]
        shutil.copyfile(tr.path, dst / tr.path.name)
        shown.append(tid)
    contrast = workdir / "contrast"
    if contrast.exists():
        shutil.rmtree(contrast)
    pairs = contrastive_pairs(traces, set(cluster), params.pairs) if params.pairs else []
    for i, (good, bad) in enumerate(pairs):
        d = contrast / f"pair_{i}_{good.task_id}"
        d.mkdir(parents=True)
        shutil.copyfile(good.path, d / ("PASS__" + good.path.name))
        shutil.copyfile(bad.path, d / ("FAIL__" + bad.path.name))
    scrub_confirm_traces(workdir, confirm_ids)
    return {"tasks": shown, "pairs": [g.task_id for g, _ in pairs]}


def cross_trace_summary(cluster, failed: dict) -> str:
    """Terms that recur across the cluster's failing traces (deterministic; no model call)."""
    per = [tokens(" ".join(t.signature for t in failed[tid])) for tid in cluster]
    counts: dict = {}
    for s in per:
        for w in s:
            counts[w] = counts.get(w, 0) + 1
    common = sorted((w for w, c in counts.items() if c >= 2), key=lambda w: (-counts[w], w))[:25]
    lines = [f"# Cross-trace summary ({len(cluster)} failing tasks)", "",
             "Terms that appear in the failure text of 2 or more of these tasks:", ""]
    lines += [f"- {w} (in {counts[w]} of {len(per)})" for w in common] or ["- (no recurring terms)"]
    return "\n".join(lines) + "\n"


CONSOLIDATE_NOTE = (
    "Consolidation mode: for each trace under trajectories/ write one short lesson to "
    "notes/lessons.md (what went wrong, in general terms). Then merge the lessons that recur across "
    "two or more distinct traces into ONE general edit. Do not encode any single task's specifics. "
    "See cross_trace_summary.md for recurring terms.")


def build_instructions(ctx, adapter_val, cluster, *, parent_dir: Path, algorithm: str, params: HC2Params,
                       shown: dict, rejected_notes, retry_note: str = "") -> str:
    label = f"failure cluster of {len(cluster)} task(s) on the feedback stratum"
    extra = [
        "This proposal is one of several made in parallel from the same incumbent; each targets a "
        "DIFFERENT failure cluster, so address only the cluster below. The traces in trajectories/ are "
        "fresh failures of the incumbent; contrast/ holds pass/fail pairs of the same task.",
        "Cluster tasks: " + ", ".join(map(str, cluster[:20])),
    ]
    if rejected_notes:
        extra.append("Recent rejected edits (do not repeat them or their near-duplicates):\n"
                     + "\n".join(f"- {n}" for n in rejected_notes))
    if params.consolidate:
        extra.append(CONSOLIDATE_NOTE)
    if retry_note:
        extra.append(retry_note)
    return ctx.instructions(adapter_val, list(cluster), label, algorithm=algorithm,
                            parent_dir=parent_dir, extra="\n\n".join(extra))


# ---- diversity gate -----------------------------------------------------------------------------

_SKIP_DIRS = {"trajectories", "contrast", "guidance", "notes", "__pycache__"}
_SKIP_FILES = {"INSTRUCTIONS.md", "MEMORY.md", "STATE.md", "LEDGER.md", "JOURNAL.md", "RUNMAP.md",
               "PROCESS.md", "cross_trace_summary.md", "DIAGNOSIS.json"}


def _files(root: Path) -> dict:
    from .harness import _SNAPSHOT_IGNORE      # the optimizer's injected scratch is not part of the edit
    skip = _SKIP_DIRS | set(_SNAPSHOT_IGNORE)
    out = {}
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if not f.is_file() or rel.parts[0] in skip or rel.name in _SKIP_FILES \
                or rel.parts[0].startswith("."):
            continue
        try:
            out[str(rel)] = f.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            out[str(rel)] = [f"<binary {f.stat().st_size}>"]
    return out


def diff_lines(parent_dir: Path, cand_dir: Path) -> frozenset:
    """The set of changed lines (``+``/``-`` tagged, whitespace-normalised) of cand vs parent."""
    a, b = _files(Path(parent_dir)), _files(Path(cand_dir))
    changed = set()
    for rel in sorted(set(a) | set(b)):
        for ln in difflib.unified_diff(a.get(rel, []), b.get(rel, []), lineterm="", n=0):
            if ln[:1] in "+-" and not ln.startswith(("+++", "---")) and ln[1:].strip():
                changed.add(f"{rel}\t{ln[0]}" + " ".join(ln[1:].split()))
    return frozenset(changed)


def _shingles(lines: frozenset, n: int = 3) -> frozenset:
    """Word n-grams within each changed line (a short line is one shingle), tagged by file and +/-."""
    out = set()
    for ln in lines:
        head, _, body = ln.partition("\t")
        w = body[1:].split()
        out.update((head, body[:1], " ".join(w[i:i + n])) for i in range(max(1, len(w) - n + 1)))
    return frozenset(out)


def diff_overlap(x: frozenset, y: frozenset) -> float:
    """Similarity of two diffs, 0.0 when either is empty: the larger of the Jaccard of the changed lines
    and the Jaccard of their word 3-grams, so a lightly reworded copy of an edit still counts."""
    if not x or not y:
        return 0.0
    lines = len(x & y) / len(x | y)
    sx, sy = _shingles(x), _shingles(y)
    return max(lines, len(sx & sy) / len(sx | sy))


def near_duplicate(diff: frozenset, others, threshold: float):
    """Index of the first diff in ``others`` overlapping ``diff`` at/above ``threshold``, else None."""
    for i, o in enumerate(others):
        if diff_overlap(diff, o) >= threshold:
            return i
    return None


class RejectedDiffs:
    """The last diffs that did not win, persisted in the run dir so a resumed run keeps its memory."""

    def __init__(self, run_dir):
        self.path = Path(run_dir.root) / "hc2_rejected_diffs.json"

    def load(self) -> list:
        try:
            return [frozenset(d["lines"]) for d in json.loads(self.path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, KeyError):
            return []

    def add(self, cid: str, diff: frozenset) -> None:
        try:
            cur = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cur = []
        cur.append({"cid": cid, "lines": sorted(diff)})
        self.path.write_text(json.dumps(cur), encoding="utf-8")

    def last(self, n: int) -> list:
        return self.load()[-n:] if n > 0 else []


# ---- the loop -----------------------------------------------------------------------------------

def _pooled_split_result(traces, tasks):
    """The incumbent's pooled F draws as a ``SplitResult`` for the shared prompt template."""
    from .loop import SplitResult
    by: dict = {}
    for t in traces:
        by.setdefault(t.task_id, []).append(t)
    rows = []
    for tid in sorted(tasks if tasks is not None else by):
        ts = by.get(tid, [])
        if ts:
            rows.append({"task_id": tid, "reward": sum(t.reward for t in ts) / len(ts),
                         "feedback": next((t.signature for t in ts if t.reward < 1.0), "")[:500]})
    mean = sum(r["reward"] for r in rows) / len(rows) if rows else 0.0
    return SplitResult(split="feedback", reward=mean, stderr=0.0, per_task=rows,
                       n_tasks=len(rows), n_scored=len(rows))


def _propose(optimizer, workdir: Path, instructions: str):
    try:
        return optimizer(workdir, instructions), None
    except Exception as e:  # noqa: BLE001 - a failed proposal costs a slot, never the run
        return getattr(e, "cost", None), str(e)


FINAL_TRIALS = 4            # ``P2Evaluator.final_estimate`` default n_trials (champion and seed each)


def final_estimate_cost(evaluator, n_trials: int = FINAL_TRIALS) -> int:
    """Rollouts the final estimate needs: champion and seed, ``n_trials`` each, on every C task."""
    return 2 * int(n_trials) * len(evaluator.confirm_ids())


def _pending_path(run_dir) -> Path:
    return Path(run_dir.root) / "hc2_pending.json"


def _save_pending(run_dir, pend: dict) -> None:
    from .rundir import _atomic_write
    _atomic_write(_pending_path(run_dir), json.dumps(pend))


def _next_cid_base(run_dir) -> int:
    """Highest ``cand_NNNN`` number on disk. Ids cannot come from ``spent.iterations``: a round is one
    iteration but K candidates."""
    best = 0
    for root in (Path(run_dir.root) / "candidates", Path(run_dir.root) / "work"):
        for p in root.glob("cand_*") if root.is_dir() else []:
            m = re.fullmatch(r"cand_(\d+)", p.name.split(".")[0])
            if m:
                best = max(best, int(m.group(1)))
    return best


def _rounds_done(run_dir) -> int:
    """One iteration is recorded per round (``count_iteration``), dropped-only rounds included."""
    return run_dir.spent.iterations


def hc2_loop(adapter, *, run_dir, optimizer, evaluator, params: HC2Params, ctx, max_iterations: int,
             store=None, algorithm: str = "hill-climb:hc2", workers: int = 1) -> dict:
    """Rounds of K parallel proposals judged by ``evaluator`` (a ``P2Evaluator``).

    Concurrency is bounded by ``workers`` in total: jobs fan out over ``thread_map(workers)`` and each
    job runs its rollouts serially (the evaluator's own ``workers`` is forced to 1), so nesting cannot
    multiply the two. An interrupted round (``hc2_pending.json`` + its ``p2/r*.json``) is resumed on
    entry, not re-proposed. One round counts as one iteration and one stall step.
    """
    from . import harness
    rejected, history, store = harness._init_memory_store(run_dir, store)
    rdiffs = RejectedDiffs(run_dir)
    n_rounds = params.max_rounds or max_iterations
    window = params.diversity_window or params.k
    if hasattr(evaluator, "workers"):
        evaluator.workers = 1
    env = dict(adapter=adapter, run_dir=run_dir, evaluator=evaluator, ctx=ctx, rejected=rejected,
               history=history, store=store, rdiffs=rdiffs, workers=workers, algorithm=algorithm)
    steps, stop = [], ""

    pend = _load_pending(run_dir)
    if pend:
        steps += _finish_round(pend, **env)

    while _rounds_done(run_dir) < n_rounds:
        exhausted, _ = run_dir.budget_exhausted()
        if exhausted:
            break
        # The final estimate is paid for up front, so a budget-bound search cannot spend it: hold
        # its cost while planning, release it before the estimate itself reserves.
        hold = None
        if params.final_estimate_trials > 0 and _internal_estimate_pending(run_dir):
            try:
                hold = run_dir.reserve_metric_calls(
                    final_estimate_cost(evaluator, params.final_estimate_trials), label="hc2_final_estimate")
            except Exception:  # noqa: BLE001 - cannot be held: the estimate will be skipped and recorded
                hold = None
        try:
            plan = evaluator.plan_round(params.k, incumbent_dir=run_dir.candidate_dir(run_dir.best_id))
        finally:
            run_dir.release_metric_calls(hold)
        if not plan.funded:
            stop = "budget_unfunded"
            break
        pend = _propose_round(plan.k, params, window, _rounds_done(run_dir), **env, optimizer=optimizer)
        if pend is None:
            stop = "no_failures"
            break
        steps += _finish_round(pend, **env)
    _, why = run_dir.budget_exhausted()
    internal = final_estimate(evaluator, run_dir, params.final_estimate_trials)
    result = {"algorithm": algorithm, "best_id": run_dir.best_id, "iterations": len(steps),
              "accepts": sum(1 for s in steps if s["accepted"]),
              **({"internal_estimate": internal} if internal is not None else {}),
              "stop_reason": why or stop or "max_iterations", "steps": steps}
    (Path(run_dir.root) / "summary.json").write_text(
        json.dumps({k: v for k, v in result.items() if k != "steps"}, indent=2), encoding="utf-8")
    return result


def _load_pending(run_dir):
    try:
        return json.loads(_pending_path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _internal_estimate_pending(run_dir) -> bool:
    return not (Path(run_dir.root) / "internal_estimate.json").exists()


def _prepare(adapter, run_dir, evaluator, ctx, params, view, cid, cl, *, incumbent_dir, traces, failed,
             c_ids, algorithm, notes, retry_note=""):
    """Build the workdir and the full prompt for one proposal (used for the first try AND retries, so a
    retry never loses the memory/consolidation parts)."""
    wd = Path(run_dir.root) / "work" / cid
    if wd.exists():
        shutil.rmtree(wd)
    wd.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(incumbent_dir, wd)
    ctx.inject(adapter, run_dir, wd, split=evaluator.proposer_split)
    shown = write_cluster_context(wd, cluster=cl, failed=failed, traces=traces, params=params,
                                  confirm_ids=c_ids)
    if params.consolidate:
        (wd / "cross_trace_summary.md").write_text(cross_trace_summary(cl, failed), encoding="utf-8")
    instr = ctx.augment_instructions(
        build_instructions(ctx, view, cl, parent_dir=incumbent_dir, algorithm=algorithm, params=params,
                           shown=shown, rejected_notes=notes, retry_note=retry_note), wd, run_dir)
    return wd, instr, shown


def _add_cost(costs: dict, cid: str, rep) -> None:
    if isinstance(rep, dict):
        c = costs.setdefault(cid, {"usd": 0.0, "tokens": 0})
        c["usd"] += float(rep.get("cost_usd") or 0.0)
        c["tokens"] += int(rep.get("tokens") or 0)


def _propose_round(k, params, window, round_index, *, adapter, run_dir, evaluator, ctx, rejected, history,
                   store, rdiffs, workers, algorithm, optimizer):
    """Cluster, propose in parallel, apply the diversity gate; persist ``hc2_pending.json``."""
    incumbent_id = run_dir.best_id
    incumbent_dir = run_dir.candidate_dir(incumbent_id)
    f_ids = [str(i) for i in evaluator.feedback_ids()]
    c_ids = [str(i) for i in evaluator.confirm_ids()]
    traces = read_traces(run_dir, (evaluator.proposer_split, "train", "val"), f_ids,
                         incumbent_tag_ok(incumbent_id))
    failed = failing_tasks(traces)
    batch = rotating_minibatch(failed, params.minibatch, round_index)
    clusters = split_to_k(cluster_failures(failed, batch, params.cluster_sim), k)
    if not clusters:
        return None
    base = _next_cid_base(run_dir)
    cids = [f"cand_{base + j + 1:04d}" for j in range(len(clusters))]
    view = _pooled_split_result(traces, None)
    notes = _rejected_notes(run_dir, window)
    kw = dict(incumbent_dir=incumbent_dir, traces=traces, failed=failed, c_ids=c_ids,
              algorithm=algorithm, notes=notes)
    workdirs, instr, shown = {}, {}, {}
    for cid, cl in zip(cids, clusters):
        workdirs[cid], instr[cid], shown[cid] = _prepare(adapter, run_dir, evaluator, ctx, params, view,
                                                         cid, cl, **kw)
    costs, errors = {}, {}
    with ThreadPoolExecutor(max_workers=max(1, min(len(cids), workers))) as pool:
        for cid, (rep, err) in zip(cids, pool.map(lambda c: _propose(optimizer, workdirs[c], instr[c]), cids)):
            _add_cost(costs, cid, rep)
            errors[cid] = err

    # diversity gate: against the last K rejected diffs and against earlier proposals this round
    recent = rdiffs.last(window)
    diffs, dropped = {}, {}
    for cid, cl in zip(cids, clusters):
        d = diff_lines(incumbent_dir, workdirs[cid])
        for attempt in range(params.retries + 1):
            if not d:
                break
            if near_duplicate(d, recent + list(diffs.values()), params.diversity_threshold) is None:
                break
            if attempt == params.retries:
                dropped[cid] = "near-duplicate of a recent or sibling diff"
                break
            note = ("Your previous attempt duplicated an edit that was already tried. Make a "
                    "different kind of edit, aimed at a different root cause in this cluster.")
            workdirs[cid], i2, shown[cid] = _prepare(adapter, run_dir, evaluator, ctx, params, view, cid,
                                                     cl, retry_note=note, **kw)
            rep, err = _propose(optimizer, workdirs[cid], i2)
            _add_cost(costs, cid, rep)            # every attempt is paid for
            errors[cid] = err
            d = diff_lines(incumbent_dir, workdirs[cid])
        if cid in dropped:
            continue
        if not d:
            dropped[cid] = "empty diff" + (f" (optimizer error: {errors[cid][:200]})" if errors[cid] else "")
            continue
        diffs[cid] = d
    for c in costs.values():
        run_dir.update_spent(optimizer_usd=c["usd"], optimizer_tokens=c["tokens"])
    pend = {"incumbent": incumbent_id, "cids": cids, "round_id": None, "dropped": dropped, "shown": shown,
            "diffs": {c: sorted(d) for c, d in diffs.items()}, "recorded": []}
    _save_pending(run_dir, pend)
    return pend


def _verdicts(d, cls) -> dict:
    return {c: cls(**v) for c, v in (d or {}).items()}


def _open_or_resume(evaluator, run_dir, pend, live, incumbent):
    """The round of this pending set: the one already on disk if there is one, else a new one."""
    if pend.get("round_id"):
        return evaluator.resume_round(pend["round_id"])
    pdir = evaluator._round_dir()   # moved under private/ by the gate; ask it, do not hard-code
    for f in sorted(pdir.glob("r*.json"), reverse=True) if pdir.is_dir() else []:
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if set(st.get("candidates", {})) == set(live):
            return evaluator.resume_round(st["round_id"])
    return evaluator.open_round(live, (pend["incumbent"], run_dir.candidate_dir(pend["incumbent"])))


def _drive(rnd, map_fn):
    """Run (or finish) a round. Idempotent: finished jobs are skipped, a closed round is only read."""
    from .evaluator import ConfirmVerdict, RungVerdict
    st = rnd.state
    if st.get("stage") == "closed":
        return (_verdicts(st["rungs"].get("1"), RungVerdict), _verdicts(st["rungs"].get("2"), RungVerdict),
                _verdicts(st.get("confirms"), ConfirmVerdict))
    rung1 = rnd.run_rung(1, map_fn=map_fn)
    rung2, confirm = {}, {}
    if any(v.status == "advance" for v in rung1.values()):
        rung2 = rnd.run_rung(2, map_fn=map_fn)
        if any(v.status == "advance" for v in rung2.values()):
            confirm = rnd.confirm(map_fn=map_fn)
    return rung1, rung2, confirm


def _finish_round(pend, *, adapter, run_dir, evaluator, ctx, rejected, history, store, rdiffs, workers,
                  algorithm):
    """Evaluate (or resume) a proposed round, apply its champion, record every candidate once."""
    from . import harness
    from .evaluator import thread_map
    cids = pend["cids"]
    workdirs = {c: Path(run_dir.root) / "work" / c for c in cids}
    live = {c: workdirs[c] for c in cids if c in pend["diffs"]}
    rung1 = rung2 = confirm = {}
    champion = None
    if live:
        rnd = _open_or_resume(evaluator, run_dir, pend, live, None)
        if pend.get("round_id") != rnd.round_id:
            pend["round_id"] = rnd.round_id
            _save_pending(run_dir, pend)
        rung1, rung2, confirm = _drive(rnd, thread_map(workers))
        champion = rnd.champion
    if champion and run_dir.best_id != champion:
        run_dir.snapshot(champion, workdirs[champion], ignore=_snap_ignore(harness))
        run_dir.set_best(champion)
    statuses = {c: _status(pend, c, rung1.get(c), rung2.get(c), confirm.get(c)) for c in cids}
    round_indecisive = champion is None and all(s[2] for s in statuses.values())
    lead = champion or next((c for c in cids if c in live), cids[0])
    steps = []
    for cid in cids:
        if cid in pend["recorded"]:
            continue
        status, reason, indecisive = statuses[cid]
        steps.append(_record(
            run_dir, harness, cid, pend["incumbent"], workdirs[cid], status, reason,
            indecisive=(round_indecisive if cid == lead and champion is None else indecisive),
            accepted=cid == champion, counts=cid == lead, rv=rung2.get(cid) or rung1.get(cid),
            ctx=ctx, rejected=rejected, history=history, store=store, shown=pend["shown"][cid],
            diff=frozenset(pend["diffs"][cid]) if cid in pend["diffs"] else None, rdiffs=rdiffs))
        pend["recorded"].append(cid)
        _save_pending(run_dir, pend)
    run_dir.log_event("hc2_round", incumbent=pend["incumbent"], candidates=cids,
                      dropped=sorted(pend["dropped"]), champion=champion, k=len(cids))
    _pending_path(run_dir).unlink(missing_ok=True)
    return steps


def _snap_ignore(harness):
    return tuple(harness._SNAPSHOT_IGNORE) + ("contrast", "notes", "cross_trace_summary.md")


def _status(pend, cid, v1, v2, cv):
    """(status, reason, indecisive) for one candidate. Only the C verdict BIT is ever written."""
    if cid in pend["dropped"]:
        return "dropped", f"hc2 diversity gate: {pend['dropped'][cid]}", True
    if cv is not None:
        return cv.status, f"P2 confirmation {cv.bit()}", cv.status == "unconfirmed"
    rv = v2 or v1
    if rv is not None:
        return rv.status, f"P2 rung {'2' if v2 else '1'}: {rv.status}", rv.status == "unfunded"
    return "unevaluated", "not evaluated", True


def _rejected_notes(run_dir, n: int) -> list:
    p = Path(run_dir.rejected_path)
    try:
        recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    return [f"{r.get('candidate_id')}: {r.get('summary', '')[:160]} ({r.get('reason', '')[:120]})"
            for r in recs[-n:]]


def _record(run_dir, harness, cid, parent_id, workdir, status, reason, *, indecisive, accepted, counts, rv,
            ctx, rejected, history, store, shown, diff, rdiffs) -> dict:
    """Write one candidate's record. Only the round's lead candidate counts as an iteration / stall step."""
    run_dir.snapshot(cid, workdir, ignore=_snap_ignore(harness))
    harness.record_iteration(run_dir, workdir, cid, parent_id=parent_id, accepted=accepted,
                             reason=reason, val=None, parent_val=None, indecisive=indecisive,
                             memory_skill=ctx.memory_skill, count_iteration=counts,
                             hc2={"status": status, "tasks": shown["tasks"], "pairs": shown["pairs"],
                                  "feedback_mean": getattr(rv, "mean", None)}, round_status=status)
    summary = f"candidate {cid} ({status})"
    if accepted:
        if history is not None:
            history.add(cid, summary, getattr(rv, "mean", 0.0) or 0.0)
    elif not indecisive and rejected is not None:
        rejected.add(cid, summary, reason, getattr(rv, "mean", None))
    if not indecisive and not accepted and diff is not None:
        rdiffs.add(cid, diff)
    if store is not None and not indecisive:
        store.commit(f"hc2 {'ACCEPT' if accepted else 'reject'} {summary}",
                     tag=("best" if accepted else None), accepted=accepted)
    return {"candidate_id": cid, "accepted": accepted, "status": status, "reason": reason,
            "workdir": str(workdir)}


def final_estimate(evaluator, run_dir, n_trials: int = FINAL_TRIALS):
    """Champion vs seed on C with fresh trials, once, as a FINAL phase after the search.

    Same file and schema as ``harness._internal_estimate`` (which ``hill_climb_loop`` uses), so whichever
    runs first wins and the estimate is never paid for twice. It is charged against ``max_metric_calls``
    through the evaluator's reservation (``budget_reserve``); when the remaining budget cannot fund it the
    estimate is skipped and recorded, never run over budget. ``None`` when the champion is the seed.
    ``n_trials == 0`` disables it: nothing is reserved or run, and ``skipped="disabled"`` is logged."""
    from .rundir import BudgetExhausted
    if n_trials <= 0:
        run_dir.log_event("p2_final_estimate", skipped="disabled")
        return {"skipped": "disabled"}
    best = run_dir.best_id
    path = Path(run_dir.root) / "internal_estimate.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if not best or best == "seed":
        return None
    left = run_dir.remaining_metric_calls()
    need = final_estimate_cost(evaluator, n_trials)
    try:
        if left is not None and need > left:
            # Both arms or neither: running the champion arm alone would spend and then fail.
            raise BudgetExhausted(f"the final estimate needs {need} rollouts, {left} remain")
        out = evaluator.final_estimate(run_dir.candidate_dir(best), run_dir.candidate_dir("seed"),
                                       n_trials=n_trials)
    except BudgetExhausted as e:
        out = {"skipped": "budget", "reason": str(e)[:200]}
        run_dir.log_event("p2_final_estimate", skipped="budget")
    out = {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
           for k, v in out.items()}
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
