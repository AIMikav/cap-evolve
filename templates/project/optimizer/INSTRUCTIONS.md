# Optimize the capability — ship several REAL, SAFE, VERIFIED fixes this iteration

{{FOCUS_SUMMARY}}

GOAL: raise the eval score as much as you can THIS iteration, then STOP. The prompt AND
the tools are equally fair game. You have a self-eval — `./ablate` — to MEASURE each
edit's real effect before you keep it (see ABLATE-AND-MERGE); the harness still does the
authoritative final re-score after you STOP, so `./ablate` is your filter, not the judge.

Make **AS MANY changes as survive ablation** this iteration. The trap that kept prior
runs flat was *two-way churn*: bundling several edits into one candidate where some fixed
tasks and others silently broke passing tasks, netting almost nothing. The fix is not
"fewer edits" — it is to **ablation-test each edit and ship only the ones proven to help
without regressing.** There is NO penalty for breadth once each kept edit is individually
verified. Diagnose ALL clusters, draft a fix for each, then let `./ablate` decide which stay.

## The THREE TESTS every change must pass (this is the whole game)
Before you keep any edit, confirm all three. Drop any edit that fails even one.
1. **REAL** — it targets a cluster that is FAILING in THIS iteration's `./trajectories/`
   (reward 0, partial-credit, or communication/omission). Never edit for a hypothetical
   problem, never touch a path only used by already-PASSING tasks.
2. **SAFE (bounded blast radius)** — the real regression question is *behavioral*:
   **would this edit change what the agent DOES on ANY currently-passing task?** Not
   "does a passing task call this tool" — "does the agent now take a different action,
   or newly ACT where it correctly REFUSED / escalated". Two blast-radius classes:
   - **BOUNDED** — an in-body guard/computation that fires ONLY on the exact violating
     condition. Only already-failing inputs hit it; passing tasks are untouched by
     construction. This is the SAFE default — prefer it.
   - **UNBOUNDED** — any edit to a GLOBAL decision/permission/refusal rule in the prompt
     (loosening "X may do Y", broadening who may take an action, relaxing a
     refuse-and-escalate rule). It changes behavior across the ENTIRE decision class,
     including tasks where the stricter/original behavior was the gold answer. Allowed
     ONLY if the new behavior is correct for EVERY task in that class AND you have read
     the currently-passing tasks in the class and confirmed none relied on the old
     behavior. Otherwise it is a guaranteed regression — encode the discriminating
     CONDITION instead (see the DECISION / PERMISSION lever below).
   Name the passing tasks in each edit's blast-radius class and state which class it is.
   A regression wastes the whole candidate (the gate rejects a net-zero), not one task.
3. **VERIFIED by ablation** — you have RUN `./ablate` on this edit alone and shown its
   target task(s) improve AND no protected/passing task drops (see ABLATE-AND-MERGE). An
   edit you have not ablated is a guess — drop it or ablate it.

Ship EVERY edit that survives ablation — breadth is good once each edit is individually
verified non-regressing. Do NOT ship an un-ablated edit to pad a count, and do NOT re-add
anything `LEDGER.md` / `JOURNAL.md` show was already tried and rejected.

## Read these first (everything is in this working directory)
- `./guidance/<cap>/SKILL.md` for EACH selected capability — your edit space, levers,
  and worked examples. Read it before editing. (Tool code? also read `./guidance/sources/`
  for the data models/types so your code is correct.)
- `./guidance/diagnose/SKILL.md` — the failure-clustering method. Use it.
- `./trajectories/` — the FULL traces of the current best candidate (the step you build
  on). The `{{FAILURES}}` block below summarizes them with argument-level feedback — read
  the actual traces for the clusters you'll fix, don't rely on the summary alone.
- `./LEDGER.md` — FACTS (read-only): every prior iteration's outcome + the exact tasks it
  broke/fixed. Your SAFE test starts here — never re-introduce a change that broke a task.
- `./JOURNAL.md` — your append-only handover across the run. Skim it for what's been
  tried and refuted; APPEND your entry at the end. Never re-try a refuted idea.
- `./RUNMAP.md` + `./prior_iterations/<id>/` — prior iterations' PROCESS.md + diffs. Read
  the one(s) that touched a cluster you're about to work on, so you build on them.
- `./PROCESS.md` — your REQUIRED explainability file for THIS iteration (template inside).
- `./guidance/optimizer/<name>.md` — your agent's subagent/parallelism features (optional).
{{BENCH_REPO}}

## Process (do this, then STOP)
**Parallelism:** {{PARALLEL_NOTE}}
1. Read your capability SKILL(s) + the diagnose method + the cross-iteration files
   (LEDGER facts, JOURNAL handover, RUNMAP for clusters you'll touch).
2. Diagnose THIS iteration's `./trajectories/` ONLY (not stale signatures). Cluster ALL
   failures by shared root cause — total, partial-credit, AND communication/omission.
   RANK clusters by LEVERAGE = (# failing tasks × trials × score recoverable), biggest
   first.
3. For each top cluster, pick the right lever by FAILURE TYPE (next section) and draft
   the edit. Run it through the THREE TESTS.
4. **ABLATE-AND-MERGE (mandatory): use `./ablate` to keep only the edits that work,
   then merge them into this ONE candidate** (full protocol below). This is the step
   that stops two-way churn.
5. Fill `PROCESS.md` and APPEND your entry to `JOURNAL.md`. STOP.

## Choose the lever by FAILURE TYPE
Pick the strongest lever YOUR capability's edit space offers (see `./guidance/<cap>/`).
The levers below are written for a TOOLS capability; for a prompt-only capability
(system-prompt / skill-package) use the structural-prose equivalent noted in each.

- **RULE VIOLATION** — the agent breaks a rule/precondition/formula it could already
  follow. **Default strong lever (tools): move the rule INTO THE CODE BODY of the
  EXISTING tool that governs it** — an in-body validation / normalization / computation
  that raises an ACTIONABLE error or returns the corrected value, scoped to fire ONLY on
  the violating condition. This is the highest-yield, lowest-regression edit and is what
  drove the best prior results — reach for it first. *Example:*
  ```
  def book(record_id, amount, payment_id):
  +   methods = {m["id"] for m in get_record(record_id)["payment_methods"]}
  +   if payment_id not in methods:                      # fires only on the violation
  +       raise ValueError(f"payment_id {payment_id!r} not on file; available={sorted(methods)}")
      return _backend.book(record_id, amount, payment_id)
  ```
  (Prompt capability: make the rule unmissable — a checklisted step / worked counterexample.)
- **CAPABILITY GAP / ACTION STALL** — the agent has NO reliable way to do the thing (a
  hard-ZERO cluster needing a real compute / composite / discriminating-predicate tool),
  or it narrates/confirms a multi-step action then never executes it. **Prose does
  NOTHING for a hard zero** — a 0.00 task stays 0.00 after any docstring/prompt reword;
  it needs a TOOL the agent will CALL that changes the graded state. (tools) ADD a NEW
  code-bearing tool that closes the gap — a composite atomic-WRITE tool whose body
  performs the whole action via the existing primitives (then REMOVE the raw primitives
  so it can't be skipped), or a loop/validation tool. Add a new tool ONLY when it closes
  a real gap AND the agent will call it AND it changes the graded outcome — NOT a
  read/compute/summary helper that a guard or a prompt line would subsume, and never to
  hit a quota. (Prompt capability: an explicit, ordered, unavoidable procedure.)
- **KNOWLEDGE GAP** — a format/criterion/fact the agent genuinely cannot derive →
  prose: a precise prompt or docstring rule. Don't restate a rule the agent already has;
  that's a rule-violation (use code), not a knowledge gap.
- **DECISION / PERMISSION (ACT vs REFUSE)** — the agent made the wrong call on a
  decision the policy governs: it ACTED where it should have refused/escalated, or
  refused where it should have acted. **This is the most dangerous cluster to fix
  wrong.** NEVER loosen, broaden, or alter a GLOBAL decision/permission/refusal rule in
  the prompt to fix it — a global prose change (e.g. "restricted records MAY now be
  modified") flips behavior for the WHOLE class and regresses every currently-passing task where
  the original, stricter behavior was the gold answer (this exact mistake sank a prior
  run). Instead encode the EXACT discriminating CONDITION that separates the qualifying
  cases, **ideally in CODE** — an in-body guard on the tool that owns the action, which
  refuses/raises ONLY when the precise policy predicate is/isn't met (bounded blast
  radius: only the qualifying cases change). If it truly cannot be code, add an ADDITIVE
  prompt rule that NARROWS (states the exact predicate) — never one that LOOSENS.

Also improve **tool RETURN values / error messages** (actionable: what's wrong + valid
options + next step) when a recoverable error stranded the agent — this is high-leverage
and low-risk. Never delete a needed rule; change/consolidate instead.

## ABLATE-AND-MERGE (the core loop — this is what makes breadth safe)
`./ablate` scores a candidate dir on a TASK SUBSET against the parent baseline
(`baseline_pertask.json`) and prints, per task, baseline → candidate → delta → verdict.
It is a self-check only (it never changes the run's score/budget); the harness still does
the authoritative final re-score after you STOP. Usage:
```
./ablate [--cand DIR] [--trials N] TASK_ID [TASK_ID ...]
```
For EACH edit, the targets are its FAILING task(s); the protected set is 1–3 currently
PASSING tasks in its blast radius (same tool / same decision class).

1. **Build a clean single-edit copy.** Copy the pristine parent into a scratch dir, apply
   ONLY this one edit there. (The parent's pristine capability is your starting workdir
   before you edit; keep a clean copy, e.g. `cp -r` the unedited files aside first, or
   build each edit in its own copy.)
2. **Ablate the edit alone:** `./ablate --cand <scratch> <targets> <protected>`.
   - KEEP the edit iff every target improves (delta > 0) AND no protected task regresses
     (no `REGRESSED` line). 
   - DROP it if a target stays flat (it doesn't actually fix the cluster — prose on a hard
     zero, a guard that never fires) OR any protected task drops (a real regression).
   - A `flat` target means "not a real fix" — re-diagnose or drop; don't ship hope.
3. **Merge survivors:** apply all KEPT edits together into your working candidate (this
   directory). Then **ablate the union**: `./ablate <all targets> <all protected>`. If a
   task that was fine for each edit solo now regresses, two edits CONFLICT — drop the
   lower-leverage one and re-ablate the union until clean.
4. Ship the merged, union-clean set. STOP.

Decision/permission edits: the protected set MUST include passing tasks in the SAME
decision class (where the gold answer is to refuse/escalate), so ablation catches an
edit that makes the agent newly ACT where it should not. If you cannot name such tasks,
the edit is UNBOUNDED — replace it with a coded discriminating-condition guard scoped to
the violating cases only, then ablate that.

Record one line per edit in PROCESS.md, e.g.
`update_X guard: targets {t7:+0.8,t24:+0.6} protected {t2:0,t6:0} → KEPT` /
`prompt rule: target {t14:+0.0} → DROPPED (flat, no real fix)`.
An edit with no ablation line is unverified — ablate it or drop it.

## NON-OVERFITTING (every edit must GENERALIZE)
Every edit encodes a GENERAL rule that holds across the whole class of inputs — NEVER a
literal that special-cases one task (its id, target, name, or expected answer). A guard
fires on the general condition ("the id is not in the user's records", "the record is in
a state that forbids this action"), NOT `if record_id == "<TASK_SPECIFIC_ID>"`. ALLOWED: constants the policy/domain
defines (the policy's stated current date, a fixed fee/threshold, a domain enum). Use
per-task specifics and any ground-truth in the traces ONLY to understand the failure
CLASS, then write the general fix.

## Handover (REQUIRED before you STOP)
- **PROCESS.md** (this iteration): the ranked cluster list (with leverage + RULE/GAP/
  KNOWLEDGE tag), every edit + its lever, the per-edit ABLATION line (targets {Δ} /
  protected {Δ} → KEPT or DROPPED), the union-ablation result, what you deliberately
  skipped and why, and (if you used subagents) that you did.
- **JOURNAL.md** (append one entry below the marker; never edit earlier entries):
  what I tried (1 line/change) · what WORKED (only on a real gated gain; cite task ids/Δ)
  · what REGRESSED and the verdict · refuted hypotheses (never re-test) · high-value
  clusters not yet cracked + designs already tried · plateau signal + which lever to
  switch to · focus next iteration.

{{FAILURES}}
{{PASSING}}
{{CAP_BRIEF}}
{{ALGO_BRIEF}}

## Self-check before STOP
- Every kept edit was ABLATED alone (`./ablate`): its target(s) improved and no protected
  task regressed — with its ablation line in PROCESS.md. Drop any edit you did not ablate
  or that came back flat/regressing.
- You ran the UNION ablation across all kept edits and it is regression-clean (no two-way
  churn); you dropped the lower-leverage side of any conflicting pair.
- You shipped every surviving edit, covering the top-ranked clusters — and you did NOT
  ship an un-ablated/speculative edit or re-add anything already rejected (LEDGER/JOURNAL).
- For RULE-VIOLATION clusters on a tools capability, you used in-body guards on the
  existing tools (the default strong lever), not loose prose.
- For DECISION / PERMISSION clusters you did NOT loosen or alter a global decision/
  permission/refusal rule; you encoded the discriminating CONDITION (in code where
  possible) and confirmed it does not flip the action on any passing task in the class.
- Every prompt edit is ADDITIVE knowledge the agent lacked (a fact/format/narrowing
  predicate) — never a change to a decision the agent currently gets right.
- For any hard-ZERO cluster you shipped a real tool the agent will call (prose does
  nothing for a zero).
- Any new tool closes a real gap, will be called, and changes the graded outcome — not a
  helper a guard subsumes, not quota-filling.
- No edit hardcodes a task-specific id/value/date/answer.
- PROCESS.md + JOURNAL.md are filled. Keep narration minimal; don't restate these
  instructions or explore unrelated files.
