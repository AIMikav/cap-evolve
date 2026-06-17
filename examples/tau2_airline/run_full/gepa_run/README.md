# tau2-airline — GEPA run with claude-code(opus) optimizer (autonomous)

Second end-to-end run, swapping the optimizer to **claude-code (opus)** and the
algorithm to **GEPA**, to test whether a stronger optimizer + GEPA's reflective
machinery produces an *accepted, honestly-gated* gain.

## Config
- All 50 airline tasks; honest **30/10/10 holdout** (`split_ids.json`).
- Capabilities `[system-prompt, tools]`; optimizer **claude-code(opus)**; algorithm **gepa**
  (`--max-metric-calls 300 --minibatch-size 4 --max-merges 2`); `num_trials 2`;
  `max_iterations 12`; paired gate `k_se 0.5`; tau `max_concurrency 7`; agent+user gpt-oss-120b (RITS).

## Result (honest)
- Baseline val **0.55**. 12 iterations, 116 metric-calls.
- **1 of 12** candidates (`gepa_0005`) passed GEPA's cheap minibatch gate to earn a full-val eval
  (claude-code's edits cleared the local filter that bob never did) — but on the full 10-task val it
  scored **0.55 = seed** (paired Δ̄=0.0 ≤ 0.5·SE=0.0745), so the honest gate **rejected** it.
- best=seed; **sealed test 0.70** (the seed's score on the held-out test slice; pass^1=pass^2=0.70).

## What this demonstrates
The pipeline, GEPA's two-stage economy (cheap minibatch filter → expensive full-val gate), the
per-instance Pareto frontier, and the claude-code optimizer ALL work end-to-end autonomously. The
**honest gate correctly refused to claim a gain** that wasn't statistically distinguishable from noise
on a 10-task holdout — which is the system's core value. Surfacing an *accepted* gain needs more
statistical power (a larger val / more trials, or the no-holdout 50-task fit setup the engine labels
explicitly as a fit metric) — not a weaker gate.
