---
name: system-prompt
description: Optimize an agent's system prompt or policy text — the instructions that shape its behavior. Use when the thing you want to improve is a prompt/policy file (not tools or a skill package). Covers what is safely editable, how prompt wording changes agent behavior, common failure modes (over-long preambles, conflicting instructions, missing output contracts), and what to measure. Provides concrete materialize/apply/validate handlers for prompt artifacts.
component: capability
argument-hint: "--path DIR"
allowed-tools: Read, Write, Edit, Bash
provides: [candidate]
needs: []
sources: [tau2bench]
---

# Capability: system prompt

The system prompt is the cheapest, highest-leverage parameter of most agents: a
few words can flip success on a whole class of tasks. This capability treats one
or more prompt/policy text files (`prompt.txt`, `policy.md`, `SYSTEM.md`) as the
optimizable artifact.

## What can be optimized
- **Task framing & role** — who the agent is and what "done" means.
- **Output contract** — exact format the downstream/eval expects (a frequent
  silent failure: the agent is *capable* but formats wrong).
- **Decision rules** — when to call which tool, when to ask vs. act, refusal
  rules (many agents are scored on adherence to such decision rules).
- **Few-shot exemplars / reasoning scaffolds** — added inline.

## Prose fixes KNOWLEDGE gaps, not BEHAVIORAL ones
The system prompt is the right lever when a failure is a *knowledge* gap — the
agent doesn't know the required output format, a decision criterion, or a rule.
Telling it teaches it, and behavior changes. The prompt is the WRONG lever when a
failure is *behavioral* — the agent already "knows" what to do (it analyzes,
explains, even confirms) but then skips the action (e.g. stalls before issuing a
write and stops). More prose does not fix a behavior the model already agreed to
and declined; that class of failure belongs in the agent's tools/code (see the
[`tools`](../tools/SKILL.md) capability — encapsulate the action so it can't be
skipped). Diagnose every cluster as KNOWLEDGE (fix here) vs BEHAVIORAL (fix in
code) before reaching for a prompt edit.

## How agents use it
The prompt is prepended to context every turn. Agents read it literally and are
sensitive to ordering, contradictions, and verbosity. Long preambles dilute
attention; conflicting instructions resolve unpredictably.

## Common problems (see references/pitfalls.md)
- Over-long, redundant instructions → worse, not better.
- Missing/loose output contract → correct content, wrong shape, zero reward.
- Instructions that fight the model's defaults → inconsistent behavior.

## Handlers (scripts/abstract.py)
`materialize(dir) -> {file: text}` · `apply(dir, edits) -> report` ·
`validate(dir) -> {ok, files, problems}`. Edit ops: `set`, `append`,
`ensure_contains`. A project adapter's `apply` can call these directly.

## Optimizing it each iteration (analyze → ideate → edit)
The optimizer should **analyze before editing**: from the traces + the current
prompt, identify (a) the recurring failures clustered by root cause (the rule the
agent keeps breaking) and (b) the good behavior seen only on some trials that
should be made consistent; then make ONE targeted prompt edit that fixes the
biggest cluster and reinforces (b) — sharpen or correct the offending rule rather
than appending more preamble. Be economical: one good edit, then stop.

## How to run
```
python scripts/check.py
python scripts/run.py --path <capability_dir>     # prints the candidate + validity
```

## References
- `references/concepts.md` — why prompts are high-leverage; what to optimize; pitfalls.
