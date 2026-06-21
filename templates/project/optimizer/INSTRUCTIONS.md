# Optimize the capability — make the largest improvement you can this iteration

{{FOCUS_SUMMARY}}

GOAL: maximize the eval score. Make the biggest, most generalizing improvement you
can this iteration. The prompt AND the tools are EQUALLY fair game — pick whatever
fixes the most failure clusters. Then STOP (the harness re-scores you; don't re-run
evaluation yourself).

EFFORT: scale your analysis depth and effort to the number and difficulty of the
failing trajectories. Few, easy failures → still address each one. Many or
hard failures → go deeper; if your agent supports parallel sub-agents / worktrees
(see `./guidance/optimizer/<name>.md`), spawn one per cluster to analyze concurrently,
then synthesize.

## STEP 0 — read before analyzing
Before you look at ANY trajectory or begin diagnosis, READ IN FULL:
- `./guidance/<cap>/SKILL.md` for EVERY selected capability — the edit space, the
  worked examples, the boundaries. (There may be more than one capability; read each.)
- `./guidance/optimizer/<name>.md` — YOUR OWN agent's feature doc (subagent /
  parallelism / worktree mechanics). You will need it for the two-phase plan below.
Do NOT begin diagnosis until you have read these in full. Skipping STEP 0 is how
prior iterations missed the in-code fix path and shipped prose-only patches.
After reading, write the capability's full CHANGE MENU (the edit classes from the
SKILL) into PROCESS.md, and as you diagnose, MAP each cluster to a change class and a
tag (RULE-VIOLATION → existing-tool-body guard; CAPABILITY-GAP / STALL → NEW tool;
KNOWLEDGE → prose). This mapping is what stops every fix collapsing onto docstrings.

If `./trajectories/` include ground-truth / expected actions / a reward breakdown
(some benchmarks copy these into the traces; you will NOT always have them), USE
them to localize the exact defect — which action / argument / value was expected vs
what the agent did. Ground truth is for UNDERSTANDING the failure class only; keep
your fix GENERAL (see the non-overfitting guardrail below) — never copy a gold
value into the prompt or tool code.

## Read these first (everything is in this working directory)
- `./guidance/<cap>/SKILL.md` — the capability skill(s) you may edit. WHAT YOU CAN
  CHANGE is listed there, with worked examples and edit boundaries. Read it first.
- `./guidance/sources/` — supporting source files (data models / types the tools
  import). Read them before writing tool code so your code is correct.
- `./guidance/diagnose/SKILL.md` — the failure-clustering METHOD (reflective dataset,
  group failures by shared signature). Use it.
- `./guidance/optimizer/<name>.md` — your agent's subagent/parallelism/worktree doc.
- `./trajectories/` — the FULL, unmodified traces of the current best candidate's most
  recent evaluation (the step you build on). Your ground truth — don't rely on the
  short feedback lines alone.
- `./LEDGER.md` — FACTS (framework, read-only): every prior iteration's outcome +
  the exact tasks it broke/fixed. Never re-introduce a change that broke a task.
- `./JOURNAL.md` — your append-only HANDOVER across the whole run. Read all of it,
  then APPEND your entry for this iteration below the marker; never edit earlier
  entries. This is how you avoid repeating refuted ideas and breaking the plateau.
- `./RUNMAP.md` + `./prior_iterations/<id>/` — every prior iteration's PROCESS.md +
  capability diff, copied in. Read the ones that targeted YOUR cluster before you
  propose, so you build on them instead of repeating them.
- `./PROCESS.md` — your REQUIRED explainability file for THIS iteration (template
  provided); fill it as you work. It is snapshotted with your candidate.
{{BENCH_REPO}}

## Process (do this, then STOP)
1. STEP 0 above (read SKILL.md for every capability + your optimizer's feature doc).
2. Read the current capabilities, the guidance above, and the cross-iteration files —
   LEDGER.md (facts), the whole JOURNAL.md (handover), and the RUNMAP.md +
   ./prior_iterations/ entries for any cluster you'll touch — to understand what has
   already been tried and what to build on.
3. Analyze THIS step's trajectories in `./trajectories/` with the diagnose method.
   Diagnose against THIS candidate's CURRENT trajectories ONLY — never against past
   clusters, prior-iteration notes, or stale signatures. The failures you fix must be
   the ones present in the traces in front of you. Find the MANY recurring issues —
   total failures (reward 0), partial-credit failures (graded between 0 and 1), and
   communication/omission failures (the agent did the work but failed to report or
   confirm it). Also note GOOD-but-INCONSISTENT behaviors (pass on some trials, fail on
   others) to make consistent. Name each cluster, its tasks, and its shared cause.
   RANK clusters by (# failing tasks × trials) — failure frequency — and spend effort
   top-down; the cluster that fails the most task×trial cells is worth the most.
   Do NOT add a guard for a cluster whose tasks already PASS in the current best
   (check the "Currently PASSING" block) — diagnosing a stale/already-passing cluster
   wastes the iteration and risks regressing a passing task. Every fix must target a
   task that is FAILING in the current trajectories.
4. Fix MANY root causes in this ONE candidate (see the mandate below). Address the
   ranked clusters top-down — start with the highest (# tasks × trials) cluster — and
   cover as many as you can, not just the biggest.
5. VERIFY each fix against the failing trace it targets (the VERIFY-THE-FIX gate below).
6. Fill `PROCESS.md` (this iteration) and APPEND your entry to `JOURNAL.md` (handover),
   apply the edits, and STOP.

## Fix MANY root causes — choose the code edit by FAILURE TYPE, and ship MULTIPLE classes
For each cluster, classify it, then pick the matching edit (most iterations need
several of these, together, in ONE candidate):
- **RULE VIOLATION** (the agent breaks a rule a tool already implies) → move the rule
  INTO THE CODE BODY of the EXISTING tool it governs — an in-body validation /
  normalization / computation that raises an ACTIONABLE error or returns the corrected
  value. A rule the agent can read but breaks stays broken until code enforces it.
- **CAPABILITY GAP / ACTION STALL** (the agent lacks a way to do the thing, or it
  narrates/confirms a multi-step action then never executes it) → **ADD A NEW
  code-bearing tool**: a composite atomic-WRITE tool whose body performs the whole
  action via the existing primitives (then REMOVE_TOOLS the raw primitives so it's
  un-skippable), or a loop/validation tool. For a STALL this is the correct fix EVEN
  WHEN a write primitive exists — the primitive is exactly what the agent skips. Do
  not downgrade this to a prose "be sure to act" rule; that is the edit that has
  repeatedly failed.
- **KNOWLEDGE GAP** (a format/criterion/fact the agent genuinely cannot derive) →
  prose: a prompt or docstring rule.

**The failure mode this instruction prevents is shipping ONE change and stopping, or
leaving a STALL/capability-gap cluster as prose because "an existing tool already
exists."** Adding a NEW tool is ENCOURAGED — across a whole run that ships zero new
tools when stall clusters persist, the optimizer under-used every iteration. A strong
iteration edits SEVERAL existing tool bodies AND adds at least one new tool when a
capability-gap/stall cluster is present.

A strong iteration ships, together: (a) for each rule-violation cluster, an in-body
guard added to the EXISTING tool that governs it (validate/normalize/compute → raise
an actionable error or return the fixed value); (b) where a behavioral cluster needs a
new safe path, a validation/workflow/composite tool with REAL code (then REMOVE_TOOLS
the raw primitive via the safe wrapper-swap); (c) enriched tool RETURN values +
actionable error messages so the agent can recover; (d) corrected tool code where a
handler is wrong; (e) sharpened docs / prompt rules ONLY for genuine knowledge gaps.
Ground every change in the trajectories; never drop a needed rule
(change/consolidate/add — don't delete). Build on the current best (keep its wins).

## NON-OVERFITTING GUARDRAIL (every edit must GENERALIZE)
Every prompt/tool edit must encode a GENERAL rule/policy/validation that holds
across the whole CLASS of inputs — NEVER hardcode a literal that matches or
special-cases a SINGLE task (its specific id, target, name, or expected answer). A
guard must fire on the GENERAL condition (e.g. "payment_id not in the user's
profile", "reservation already flown", "amount not a multiple of the unit"), NOT
match a task-specific literal (NOT `if destination == "SEA"`, NOT
`if reservation_id == "ABC123"`, NOT returning a particular task's answer). A change
that only helps one task (a literal special-case) is FORBIDDEN — it overfits, gets
rejected by the held-out gate, and hurts other tasks.
ALLOWED (not overfitting): constants the GENERAL policy/domain defines — the current
date the policy states (e.g. `datetime(2024, 5, 15)` when the policy says "today is
…"), a fixed threshold/limit/fee, or an enum the domain defines. Encode those freely;
they apply to every task. The line is: task-specific literal (forbidden) vs
policy-defined constant (fine). Use any per-task specifics (and any ground-truth in
`./trajectories/`) ONLY to understand the failure CLASS, then write the general fix.

## VERIFY-THE-FIX gate (MANDATORY — do this for EACH fix before you finish)
A fix that does not change the failing trace is not a fix. For EACH fix, before
finishing, RE-CHECK it against the failing trace it targets, by edit type:
- **In-body guard / computation:** run the tool body (or a minimal harness) on the
  EXACT arguments from the failing trajectory; confirm the guard FIRES (raises the
  actionable error) or returns the corrected value. A guard whose condition is never
  true for the failing task is dead code — DROP or REDESIGN it until it fires.
- **NEW composite / workflow tool (for a STALL):** a stall trace contains a
  NON-action, so there is no bad argument to replay — instead CONSTRUCT the inputs the
  agent SHOULD have passed (from the observed state in the trace) and run the new
  tool's body on them; confirm it executes the full action end-to-end and returns the
  finished state. Do NOT drop a new tool merely because there is no bad arg to replay.
- **Prompt/docstring (knowledge gap):** confirm the missing fact is now stated and is
  general (not a task-specific literal).

Record per fix in PROCESS.md (the verify-the-fix section), one line each, e.g.:
  - `trace <task-id> arg <x>=<bad-value> → guard now raises "<actionable msg>"`
  - `trace <task-id> stall → new tool apply_change(<reconstructed inputs>) completes the write`
A fix with no such verification line is not done.

## Two-phase parallel work — and you MUST leave a trace that it happened
Use your agent's subagent/worktree features (`./guidance/optimizer/<name>.md`) to do
this in two fan-out phases, then MERGE. **Do not describe a process that leaves no
trace.** PROCESS.md MUST record evidence the fan-out actually ran:

- **Phase 1 — DIAGNOSE (read-only, parallel).** Fan out one read-only subagent per
  trajectory-group / failure-cluster; each finds its issues concurrently and reports
  back. The MAIN agent assembles a single MASTER ISSUE LIST (each issue: cluster,
  governing tool, tag = RULE-VIOLATION / CAPABILITY-GAP / KNOWLEDGE, intended fix
  class). **Record the MASTER ISSUE LIST in PROCESS.md** — that list is the phase-1
  evidence.
- **Phase 2 — IMPLEMENT (parallel).** Fan out one subagent per ISSUE. Each makes its
  ONE targeted edit — an EXISTING-tool-body guard for a rule violation, a NEW
  composite/loop/validation tool for a capability-gap/stall, or a prose edit for a
  knowledge gap. If edits would collide on the same file, give each subagent its OWN
  worktree. The MAIN agent then MERGES every subagent's edit into ONE candidate
  (resolve conflicts, keep all the edits), records them in PROCESS.md, and STOPs.

**If you CANNOT reliably fan out** (your agent lacks subagents/worktrees, or the
fan-out did not run), you MUST instead, in the main agent: diagnose ALL failing tasks
individually, RANK the clusters by (# failing tasks × trials), then fix the top-N —
and SAY SO explicitly in PROCESS.md ("fan-out unavailable; diagnosed N failing tasks
serially, ranked, fixed top-K"). Never claim a two-phase fan-out you did not run.

See `./guidance/optimizer/<name>.md` for the exact subagent / worktree mechanism your
agent provides.

## Steering — protect the wins, don't freeze
Use the "Currently PASSING" block (appended below) and the per-task broke/fixed
columns in `./LEDGER.md` as STEERING, not as a reason to avoid editing:
  - Don't re-introduce a change that BROKE a passing task (a task a prior candidate
    dropped from passing). For each currently-passing task, make sure your edit doesn't
    change the code path / rule / tool it exercises.
  - A net gain that breaks as many tasks as it fixes is rejected — protect the passing
    set, but keep editing boldly everywhere else.
  - Non-regression is a design constraint on each INDIVIDUAL fix (scope each in-body
    guard so it only fires on the violating inputs, not on a passing task's path) — NOT
    a reason to make fewer fixes. Many well-scoped in-code guards that each protect the
    passing set is the target; one timid fix is a failure.

## Handover — two files, clean ownership (REQUIRED before you STOP)
1. **PROCESS.md** (this iteration's explainability; fill the provided template):
   the ranked MASTER ISSUE LIST (with tags), every edit made + its class, the
   VERIFY-THE-FIX lines, the subagents/features used (or serial-fallback), what to
   PRESERVE, and what you deliberately skipped + why.
2. **JOURNAL.md** (your append-only handover across the WHOLE run): APPEND one entry
   below the marker (do NOT edit earlier entries) covering:
    - What I tried (1 line per change):
    - What WORKED (only when a real gated improvement was observed; cite task ids / Δ):
    - What REGRESSED as-implemented (verdict: dead idea vs worth-redesigning, how):
    - Refuted hypotheses (proven NOT the cause — never re-test):
    - High-value clusters NOT yet cracked (and the guard/tool designs already tried):
    - Plateau signal (are the last few iters stalling? which LEVER to switch to — e.g.
      a NEW composite tool instead of yet another guard, or the prompt instead of code):
    - Focus next iteration:

{{FAILURES}}
{{PASSING}}
{{CAP_BRIEF}}
{{ALGO_BRIEF}}

## Self-check before STOP
Before finishing, count your changes by EDIT CLASS (a strong iteration ships several):
  - **Multi-kind bar:** you shipped at least TWO different edit classes this iteration
    (e.g. an existing-tool-body guard AND a new tool; or guards AND enriched returns
    AND a prompt fix). Shipping a single class (only docstrings, only one guard) is an
    under-used iteration — go back and address more clusters.
  - **New-tool bar:** if ANY cluster is a CAPABILITY-GAP or an ACTION STALL (the agent
    narrates/confirms then fails to execute), you added at least ONE new code-bearing
    tool (a composite atomic-WRITE / loop / validation tool) for it — not a prose rule.
    A run that never adds a new tool while stall clusters persist is under-using every
    iteration. New tools COUNT and are encouraged.
  - **Rule-violation bar:** you converted at least half the rule-violations you found
    into in-body guards across the EXISTING tools that own them (don't leave them as
    prose).
  - EVERY fix has a VERIFY-THE-FIX line in PROCESS.md proving the guard fires / the
    computation returns the corrected value / the new tool completes the action on the
    reconstructed inputs. A fix without a verification line is unverified — verify or drop.
  - Every fix targets a cluster FAILING in the CURRENT trajectories (ranked by
    # tasks × trials), not a stale/already-passing one.
  - Does any edit hardcode a task-specific id/value/date/answer? If so, generalize or drop.
  - PROCESS.md records the two-phase evidence (master issue list + per-issue edits), or
    explicitly says fan-out was unavailable and the serial fallback was used; JOURNAL.md
    has your appended entry.
  - Restate the goal: fix as MANY clusters as possible THIS iteration for a large gain.
    Address the remaining clusters from your PROCESS.md issue list before you stop.
Keep narration minimal; don't restate these instructions or explore unrelated files.
