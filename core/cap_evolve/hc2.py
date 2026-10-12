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
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


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
            max_rounds=int(spec.get("hc2_max_rounds", d.max_rounds)))
        if min(out.k, out.minibatch, out.per_cluster) < 1 or out.pairs < 0 or out.retries < 0:
            raise ValueError("hc2_k, hc2_minibatch and hc2_per_cluster must be >= 1; "
                             "hc2_pairs and hc2_retries >= 0")
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
    return lambda tag: tag == cid or tag.startswith(cid + "__")


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
                changed.add(rel + ln[0] + " ".join(ln[1:].split()))
    return frozenset(changed)


def diff_overlap(x: frozenset, y: frozenset) -> float:
    """Jaccard similarity of two changed-line sets; 0.0 when either is empty."""
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


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


def hc2_loop(adapter, *, run_dir, optimizer, evaluator, params: HC2Params, ctx, max_iterations: int,
             store=None, algorithm: str = "hill-climb:hc2", workers: int = 1) -> dict:
    """Rounds of K parallel proposals judged by ``evaluator`` (a ``P2Evaluator``)."""
    from . import harness
    rejected, history, store = harness._init_memory_store(run_dir, store)
    rdiffs = RejectedDiffs(run_dir)
    n_rounds = params.max_rounds or max_iterations
    window = params.diversity_window or params.k
    steps, stop = [], ""
    for r in range(n_rounds):
        exhausted, _ = run_dir.budget_exhausted()
        if exhausted:
            break
        plan = evaluator.plan_round(params.k)
        if not plan.funded:
            stop = "budget_unfunded"
            break
        incumbent_id = run_dir.best_id
        incumbent_dir = run_dir.candidate_dir(incumbent_id)
        f_ids = [str(i) for i in evaluator.feedback_ids()]
        c_ids = [str(i) for i in evaluator.confirm_ids()]
        traces = read_traces(run_dir, (evaluator.proposer_split, "train", "val"), f_ids,
                             incumbent_tag_ok(incumbent_id))
        failed = failing_tasks(traces)
        batch = rotating_minibatch(failed, params.minibatch, r)
        clusters = cluster_failures(failed, batch, params.cluster_sim)[:plan.k]
        if not clusters:
            stop = "no_failures"
            break
        base = run_dir.spent.iterations
        cids = [f"cand_{base + j + 1:04d}" for j in range(len(clusters))]
        view = _pooled_split_result(traces, None)
        notes = _rejected_notes(run_dir, window)
        workdirs, shown = {}, {}
        for cid, cl in zip(cids, clusters):
            wd = run_dir.root / "work" / cid
            if wd.exists():
                shutil.rmtree(wd)
            wd.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(incumbent_dir, wd)
            ctx.inject(adapter, run_dir, wd, split=evaluator.proposer_split)
            shown[cid] = write_cluster_context(wd, cluster=cl, failed=failed, traces=traces,
                                               params=params, confirm_ids=c_ids)
            if params.consolidate:
                (wd / "cross_trace_summary.md").write_text(cross_trace_summary(cl, failed),
                                                           encoding="utf-8")
            workdirs[cid] = wd
        instr = {cid: ctx.augment_instructions(
            build_instructions(ctx, view, cl, parent_dir=incumbent_dir, algorithm=algorithm,
                               params=params, shown=shown[cid], rejected_notes=notes),
            workdirs[cid], run_dir) for cid, cl in zip(cids, clusters)}

        reports, errors = {}, {}
        with ThreadPoolExecutor(max_workers=max(1, min(len(cids), workers))) as pool:
            for cid, (rep, err) in zip(cids, pool.map(
                    lambda c: _propose(optimizer, workdirs[c], instr[c]), cids)):
                reports[cid], errors[cid] = rep, err

        # diversity gate: against the last K rejected diffs and against earlier proposals this round
        recent = rdiffs.last(window)
        diffs, dropped = {}, {}
        for cid in cids:
            d = diff_lines(incumbent_dir, workdirs[cid])
            for attempt in range(params.retries + 1):
                if not d:
                    break
                hit = near_duplicate(d, recent + list(diffs.values()), params.diversity_threshold)
                if hit is None:
                    break
                if attempt == params.retries:
                    dropped[cid] = "near-duplicate of a recent or sibling diff"
                    break
                shutil.rmtree(workdirs[cid])
                shutil.copytree(incumbent_dir, workdirs[cid])
                ctx.inject(adapter, run_dir, workdirs[cid], split=evaluator.proposer_split)
                cl = clusters[cids.index(cid)]
                write_cluster_context(workdirs[cid], cluster=cl, failed=failed, traces=traces,
                                      params=params, confirm_ids=c_ids)
                note = ("Your previous attempt duplicated an edit that was already tried. Make a "
                        "different kind of edit, aimed at a different root cause in this cluster.")
                rep, err = _propose(optimizer, workdirs[cid], build_instructions(
                    ctx, view, cl, parent_dir=incumbent_dir, algorithm=algorithm, params=params,
                    shown=shown[cid], rejected_notes=notes, retry_note=note))
                reports[cid], errors[cid] = rep, err
                d = diff_lines(incumbent_dir, workdirs[cid])
            if cid in dropped:
                continue
            if not d:
                dropped[cid] = "empty diff" + (f" (optimizer error: {errors[cid][:200]})" if errors[cid] else "")
                continue
            diffs[cid] = d

        for cid in cids:
            rep = reports.get(cid)
            if isinstance(rep, dict):
                run_dir.update_spent(optimizer_usd=float(rep.get("cost_usd") or 0.0),
                                     optimizer_tokens=int(rep.get("tokens") or 0))

        live = {cid: workdirs[cid] for cid in cids if cid in diffs}
        rung1 = rung2 = confirm = {}
        champion = None
        if live:
            rnd = evaluator.open_round(live, (incumbent_id, incumbent_dir))
            map_fn = _pool_map(workers)
            rung1 = rnd.run_rung(1, map_fn=map_fn)
            if any(v.status == "advance" for v in rung1.values()):
                rung2 = rnd.run_rung(2, map_fn=map_fn)
            if any(v.status == "advance" for v in (rung2 or rung1).values()):
                confirm = _run_confirm(rnd, map_fn)
            champion = rnd.champion

        for cid in cids:
            step = _record(run_dir, harness, cid, incumbent_id, workdirs[cid], dropped.get(cid),
                           rung1.get(cid), rung2.get(cid), confirm.get(cid), champion, ctx,
                           rejected, history, store, shown[cid], diffs.get(cid), rdiffs)
            steps.append(step)
        if champion:
            run_dir.set_best(champion)
        run_dir.log_event("hc2_round", round=r, incumbent=incumbent_id, candidates=cids,
                          dropped=sorted(dropped), champion=champion, k=len(cids))
    _, why = run_dir.budget_exhausted()
    return {"algorithm": algorithm, "best_id": run_dir.best_id, "iterations": len(steps), "accepts": sum(1 for s in steps if s["accepted"]),
            "stop_reason": why or stop or "max_iterations", "steps": steps}


def _pool_map(workers: int):
    def map_fn(fn, jobs):
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return list(pool.map(fn, jobs))
    return map_fn


def _run_confirm(rnd, map_fn) -> dict:
    map_fn(rnd.run_job, list(rnd.confirm_jobs()))
    return rnd.close_confirm()


def _rejected_notes(run_dir, n: int) -> list:
    p = Path(run_dir.rejected_path)
    try:
        recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    return [f"{r.get('candidate_id')}: {r.get('summary', '')[:160]} ({r.get('reason', '')[:120]})"
            for r in recs[-n:]]


def _record(run_dir, harness, cid, parent_id, workdir, dropped, v1, v2, cv, champion, ctx,
            rejected, history, store, shown, diff, rdiffs) -> dict:
    """Write one candidate's iteration record; the C verdict reaches the log only as its bit."""
    if dropped:
        status, reason, indecisive = "dropped", f"hc2 diversity gate: {dropped}", True
    elif cv is not None:
        status = cv.status
        reason = f"P2 confirmation {cv.bit()}"
        indecisive = cv.status == "unconfirmed"
    elif (v2 or v1) is not None:
        status = (v2 or v1).status
        reason = f"P2 rung {'2' if v2 else '1'}: {status}"
        indecisive = status == "unfunded"
    else:
        status, reason, indecisive = "unevaluated", "not evaluated", True
    accepted = cid == champion
    run_dir.snapshot(cid, workdir, ignore=tuple(harness._SNAPSHOT_IGNORE) + ("contrast", "notes", "cross_trace_summary.md"))
    rv = v2 or v1
    harness.record_iteration(run_dir, workdir, cid, parent_id=parent_id, accepted=accepted,
                             reason=reason, val=None, parent_val=None, indecisive=indecisive,
                             memory_skill=ctx.memory_skill,
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
