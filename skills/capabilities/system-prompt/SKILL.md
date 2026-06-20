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
- **Role line** — a single sentence stating who the agent is. A known cheap win:
  one role sentence focuses behavior and tone.
- **Task framing** — what "done" means.
- **Output contract** — exact format the downstream/eval expects (a frequent
  silent failure: the agent is *capable* but formats wrong). **Diagnose
  output-shape failures first** — right content / wrong shape scores zero, and is
  the cheapest class to fix.
- **Decision rules** — when to call which tool, when to ask vs. act, refusal
  rules (many agents are scored on adherence to such decision rules).
- **Few-shot exemplars / reasoning scaffolds** — 3–5 diverse, relevant examples
  wrapped in `<example>` tags steer format (caveat: long example dumps can hurt
  reasoning models — keep it to a handful).

## How to write the edit (authoring rules)
These are *how* to phrase a prompt edit so it actually changes behavior:

- **State instructions positively — say what TO do, not just what not to do.**
  "Respond in flowing prose paragraphs" beats "don't use markdown." Positive
  phrasing gives the model a target; prohibitions only fence off one wrong path.
- **Explain the WHY, not bare MUSTs.** A rule with its reason generalizes; a bare
  `MUST`/`CRITICAL` does not. "Never use ellipses" works far better as "your output
  is read by a TTS engine that can't pronounce ellipses." Teach the optimizer to
  write the reason, not the command.
- **Model-sensitivity caveat — sometimes the fix is to REMOVE or soften an
  instruction.** Newer models over-comply: stale anti-laziness phrasing
  (`CRITICAL`/`MUST`/`ALWAYS`) now causes over-eagerness and over-engineering.
  Prefer plain "Use … when …". If a cluster shows over-doing rather than
  under-doing, the edit is to *cut or soften* an instruction, not add one.
- **Ordering / structure.** Put long context first and the query / output contract
  last (end-placement can lift quality on long inputs); separate
  instructions / context / examples with lightweight `<xml>` tags so the model
  doesn't conflate them.
- **CLARIFY, DON'T INVENT.** A prompt edit may only *clarify or reorganize* rules
  already present (or grounded in a source the prompt cites) — never add a new
  normative rule, exception, or workaround that conflicts with, or isn't supported
  by, the existing instructions. Unsupported invented rules are the #1 regressor.
  If two existing rules conflict, resolve toward the more restrictive one.

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
