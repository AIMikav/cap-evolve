# tau2-airline — full end-to-end runs (evidence)

Two real, fully-autonomous runs of the pipeline on tau2-bench airline, with the
agent **and** user simulator both `watsonx/openai/gpt-oss-120b` via IBM RITS. Both
were driven entirely by `cap-evolve run` (no intervention): `check → baseline →
<algorithm> → finalize → report → dashboard`.

| run | algorithm | optimizer | split | result |
|-----|-----------|-----------|-------|--------|
| [`hillclimb_run/`](hillclimb_run/)   | `hill-climb --focus all` | ibm-bob          | 12/6/6 holdout | baseline val 0.583 → candidates honestly rejected (within 1·SE) → best=seed; sealed test **0.417** |
| [`gepa_run/`](gepa_run/) | `gepa`                 | claude-code(opus) | 30/10/10 holdout | baseline val 0.55; 1/12 passed the minibatch gate, no significant full-val gain → best=seed; sealed test **0.70** |

Each dir holds the self-contained `dashboard.html` (KPI strip, cumulative-best stair,
tasks×iterations heatmap, lineage, optimizer-vs-runner cost/tokens), `report.md`,
`events.jsonl`, and `split_ids.json`.

## What these demonstrate
The pipeline, both algorithms, the per-instance Pareto frontier + minibatch economy
(GEPA), and the optimizer-agnostic optimizer layer all run **end-to-end autonomously**
on a real LLM benchmark — and the **honest paired gate correctly declines** marginal,
within-noise gains on small held-out val sets. That refusal *is* the point: cap-evolve
won't report a gain it can't distinguish from noise.

## Reproduce
Both runs were launched like this (configure the spec per the table above):

```bash
REPO=/path/to/cap-evolve; PROJECT=$R/.capevolve/project
export CAPEVOLVE_CORE=$REPO/core PYTHONPATH=$REPO/core:$PROJECT/adapters
export CAPEVOLVE_SKILLS_DIR=$REPO/skills CAPEVOLVE_TAU2_DATA=$REPO/examples/tau2_airline/data
export TAU2_MAX_CONCURRENCY=7 TAU2_LLM_TIMEOUT=240 TAU2_INFRA_RETRIES=2
# .env (repo root) holds RITS_API_KEY (+ BOBSHELL_API_KEY for ibm-bob); values may be quoted.
python3 -m cap_evolve.cli run --spec $PROJECT/capevolve.yaml --project $PROJECT --run-ts <tag>
```

Spec knobs that matter: `algorithm_skill` (`hill-climb`/`gepa`/`skillopt`),
`optimizer_skill` (any name in `optimizers/registry.yaml`), `num_trials`,
`split_ids_file` (honest holdout vs. no-holdout fit), and `algorithm_args` for
per-algorithm flags (e.g. gepa `--max-metric-calls 300 --minibatch-size 4`).
Larger val / more trials shrink the gate's SE so a real gain can clear it.
