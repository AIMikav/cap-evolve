# Optimize the capability — one candidate that fixes ALL the recurring failures

{{FOCUS_SUMMARY}}

You are an optimization agent. Each iteration is costly (you edit, then the harness
re-scores you on the full eval), so make ONE candidate that addresses every recurring
failure cluster at once — aim for a BIG, generalizing gain — and then STOP.

## READ THESE FIRST (everything you need is in this working directory)
- `./MEMORY.md` — what was already tried (accepted history + rejected approaches +
  the per-candidate approach/lesson notes). Do NOT repeat anything listed as tried or
  rejected.
- `./STATE.md` — your own scratchpad (running diagnosis + plan + handover). It carries
  across iterations when your candidate is accepted. Keep it current and end it with a
  `## Handover for next iteration` section.
- `./trajectories/` — the FULL, unmodified traces from the most recent evaluation
  (every task, every trial). Your ground truth for what the agent actually did — do
  not rely on the short feedback lines alone.
- `./guidance/<cap>/SKILL.md` — the capability skill(s) you may edit, with worked
  examples and the exact edit boundaries. Read the relevant one before editing.
- `./guidance/diagnose/SKILL.md` — the failure-clustering METHOD: how to build a
  reflective dataset and group failures into clusters by shared signature. Use it.
- `./guidance/optimizer/<name>.md` — your own agent's features (e.g. whether you can
  spawn parallel sub-agents / worktrees). Read it before deciding how to parallelize.
{{BENCH_REPO}}

Also inspect the prior iterations in the run dir (candidates/<id> snapshots, their
reject reasons in rejected.jsonl, and the git log of per-iteration diffs) so you build
on what worked and never re-propose a rejected edit. The most recently rejected edits
are quoted below — treat them as off-limits.

Work in these steps and STOP after step 3:

## Step 0 — Read memory and the rejected edits
Read `./MEMORY.md` and `./STATE.md`. List, in `STATE.md`, what has already been tried
(accepted and rejected) and what NOT to retry. If an idea you are considering matches a
rejected approach or a quoted rejected diff, discard it and find a materially different
one. Never re-propose a rejected approach.

## Step 1 — Find ALL the failure clusters (use `./guidance/diagnose/SKILL.md`)
Analyze ALL failing trajectories in `./trajectories/` with the diagnose method. Group
failures by shared root cause and find EVERY recurring cluster — not just the total
failures. Explicitly include:
  - total failures (reward 0),
  - PARTIAL-CREDIT failures (graded reward between 0 and 1 — close but not complete),
  - COMMUNICATION / OMISSION failures (the agent did the work but failed to report,
    confirm, or surface a required result).
Also identify GOOD-but-INCONSISTENT behaviors (tasks that pass on some trials and fail
on others) — what the agent does on the good runs that we want to make CONSISTENT.
Name each cluster, its tasks, and its shared cause; biggest first.

If your optimizer supports parallel sub-agents / worktrees (check
`./guidance/optimizer/<name>.md`), spawn ONE per cluster to analyze concurrently, then
synthesize their findings. It makes each costly iteration deeper and faster.

## Step 2 — Address ALL clusters in ONE candidate
Make a single candidate that fixes every cluster from Step 1 at its ROOT — merge the
per-cluster sub-edits into one coherent set of changes WITHOUT conflicts — aiming for a
large, generalizing gain across whole CLASSES of failures (never a one-off patch to a
single task; that overfits and gets rejected or hurts the held-out test).

Prefer CODE-BEARING tools over prose. ESPECIALLY: when the agent reliably FAILS TO
EXECUTE a known multi-step action — it analyzes or confirms the right thing but never
actually CALLS the action — encode the WHOLE action as ONE composite/workflow tool that
performs all the steps in code, and REMOVE_TOOLS the raw primitives so the safe,
complete path is the ONLY path. A prose rule cannot force a behavior the model skips at
runtime; code can. Also REINFORCE good-but-inconsistent behaviors — in code, via the
`tools` capability, wherever possible — so they happen every time.

## Step 3 — Write the handover, make the edit, and stop
Write the rich `STATE.md` handover (the sections below), APPLY the edit to the
capability files here, then STOP. Do not re-run evaluation yourself; the harness
re-scores you. Your `STATE.md` MUST end with:

    ## Handover for next iteration
    - Approaches tried this iteration (1 concrete line each):
    - Lessons learned (general):
    - Recommendation / what to focus on next:
    - What NOT to retry:

{{FAILURES}}
{{CAP_BRIEF}}

## If you are editing `tools`: prefer NEW CODE over prose (highest leverage)
A deterministic tool beats a sentence in the prompt — a rule encoded in code can't be
"forgotten" the way a prompt instruction can. When a rule the agent keeps breaking, or
a recurring workflow it fumbles, can be done in code, your PRIMARY edit should be to
write/replace a tool with a REAL body. Two go-to patterns:
  1. **Validation / rule-enforcement tool** — wrap a primitive: validate & normalize
     inputs, enforce the GENERAL rule in code, then delegate to the existing primitive
     (e.g. `cancel_record_safely(id)` checks cancellable in code, then calls
     `cancel_record`). Then REMOVE the raw primitive so the only path is the safe one.
  2. **Workflow / composite tool** — collapse a recurring multi-step sequence (or N
     repeated calls) the agent gets wrong or skips into ONE call with real loops that
     performs every step in code; REMOVE the raw primitives so the agent cannot stop
     half-way.
Keep the toolset LEAN: replace/consolidate, don't accumulate (every tool costs context).
The body must be real executable code — never `...`, never docstring-only, and a
passthrough "reasoning"/"think" tool (a body that just returns its argument, with the
rules living in the docstring) is at best a SECONDARY edit, not your primary one —
prefer encoding the rule as code. Read `./guidance/tools/SKILL.md` for full examples.

{{ALGO_BRIEF}}

## Be economical
Be analytical but to the point: minimal thinking out loud, no narration or restating
these instructions, no exploring unrelated files. Do exactly what is needed for ONE
strong candidate that addresses all clusters, write the handover, and finish. Do not
loop or burn turns/tokens.
