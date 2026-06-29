# Demo — onboarding SkillsBench and optimizing its shared office skills with cap-evolve

This example takes **SkillsBench** (the first benchmark for how well agents USE skills)
from a single prompt (`PROMPT.md`) to an honest, optimized result. The capability under
optimization is the FOUR shared office-document Agent Skills the benchmark hands its
agent — `docx`, `pptx`, `xlsx`, `pdf`. Because the same four skills are deployed to every
task, improving them moves many tasks at once.

> SkillsBench is just the worked example — cap-evolve optimizes any agent capability
> (prompt, tools, or skill) against any eval.

## The pieces (what following `PROMPT.md` produced)
| File | What it is |
|---|---|
| [`adapters/adapter.py`](adapters/adapter.py) | The 4 adapter methods: `tasks` (the 10 task ids), `run_target` (one `bench eval run` of a sonnet agent in Docker with the candidate skills injected at `/skills`), `score` (binary verifier reward + gold-safe failed-test feedback from the CTRF report), `materialize` (multi-skill-package aware). |
| [`adapters/anthropic_env.py`](adapters/anthropic_env.py) | Reads the IBM Anthropic-compatible gateway creds from the repo-root `.env` (no python-dotenv dep) and exposes them for `--agent-env` propagation into the sandbox. Token never hardcoded. |
| [`seed_capability/{docx,pptx,xlsx,pdf}/`](seed_capability/) | One canonical copy of each shared skill (the most complete variant found in the cloned tasks). This single set is what the optimizer edits and what gets deployed to EVERY task. |
| [`optimizer/INSTRUCTIONS.md`](optimizer/INSTRUCTIONS.md) | Optimizer guidance scoped to **skill-package only** (description/trigger · body · references · scripts edit classes; breadth-per-iteration; non-overfitting guardrail). |
| [`capevolve.yaml`](capevolve.yaml) · [`split_ids.json`](split_ids.json) | The run spec (hill-climb · 10 iters · 1 trial · paired gate) and the pinned split (train==val = 7 tasks, test = 3 sealed). |
| [`setup.sh`](setup.sh) | Executable transcript of the onboarding: install cap-evolve, clone SkillsBench, install `benchflow`, scaffold + wire the project, run `cap-evolve check`. |
| [`smoke.sh`](smoke.sh) · [`run.sh`](run.sh) | Cheap 1-task autonomy smoke; full run + live dashboard. |

## Reproduce
```bash
bash examples/skillsbench/setup.sh   # install + onboard + GREEN cap-evolve check
bash examples/skillsbench/smoke.sh   # 1 val task, 1 trial, 1 iter — sonnet in Docker → reward
bash examples/skillsbench/run.sh     # full run (10 iters) + live dashboard
```

## The key mechanism — skill injection
`run_target` calls `bench eval run … --skill-mode with-skill --skills-dir <candidate>`.
Because `<candidate>` differs from each task's own `environment/skills`, BenchFlow STRIPS
the task's bundled skills (and the Dockerfile `COPY` of them) and mounts the candidate's
four optimized skills at `/skills` instead — so the candidate is deployed to every task
verbatim. The live candidate dir IS that `--skills-dir`.

## Honesty
- Train/val/test split once; **test scored only at finalize** (3 held-out tasks).
- Acceptance gated on **val**; train==val here (the engine logs a `splits_warning` — val is
  the fit metric).
- `score()` is deterministic (reads the recorded verifier reward; never re-runs) and the
  feedback is gold-SAFE: only the agent's own failed test names + assertion messages, never
  the oracle/gold output.
