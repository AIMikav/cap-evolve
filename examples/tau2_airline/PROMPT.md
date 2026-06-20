# The prompt — onboard tau2-bench airline as a new benchmark and optimize it

Paste this to your coding agent (Claude Code) at the cap-evolve repo root and say
**"follow RUN.md."** Intake treats this as a brand-new benchmark: the integration
step **clones + installs tau2-bench**, wires RITS, writes the adapter, runs the
`cap-evolve check` gate, then the full optimize → gate → sealed-test → report loop
with a live dashboard. Everything below is the input intake needs.

```text
Follow RUN.md to run a cap-evolve optimization. Onboard this as a brand-new
benchmark — the intake/integration step should CLONE + INSTALL it (not assume it
exists). Here is everything intake needs:

# 1. CAPABILITY TO OPTIMIZE  (a copy is edited each iteration; the original is never touched)
- type:         [system-prompt, tools]      # the airline POLICY and the TOOLS, jointly
- tools means:  edit tool docstrings/descriptions; edit tool behavior/code; and
                ADD/REMOVE tools, including composite tools that call existing tools
- seed:         tau2-bench's canonical airline policy + its airline tool set
- seed tools:   the seed tools file must be CLEAN, runnable code as intake would
                produce it — real tool bodies, no baked-in optimizer/editing
                instructions in its docstrings (what the optimizer may change to the
                tools lives in the tools capability SKILL.md, not in the seed file)
- capability_sources:  set `capability_sources` to the benchmark's data-model/types
                module(s) the tools import (here tau2's airline data_model — the source
                of FlightDB, Reservation, Passenger, Payment, etc.). cap-evolve copies
                these verbatim into the optimizer's workdir so it can write correct
                new-tool code against the real types.

# 2. BENCHMARK / DATASET  (the eval) — INSTALL IT DURING INTAKE
- benchmark:    tau2-bench, airline domain
- repo:         https://github.com/sierra-research/tau2-bench   (latest main; record the resolved commit)
- install:      git clone as a sibling dir ../tau2-bench, then `pip install -e ../tau2-bench`
- tasks:        "adapter" — the adapter loads all 50 airline tasks from tau2
                (tau2.domains.airline.environment.get_tasks)
- splits:       all 50 tasks as train = val = test  (no-holdout fit metric; the engine
                logs a splits_warning and the report flags the test number as a fit metric)

# 3. RUNNER  (the agent under test) + MODELS + CREDENTIALS
- how to run:   tau2's own batch runner (adapter.run_batch -> tau2.runner.run_tasks)
- fast eval:    ALSO implement the optional adapter method
                run_trials(tasks, ctx, *, n_trials, base_seed) -> {task_id: [Rollout, ...]}.
                Run ALL num_trials in ONE tau2 run_tasks call with num_trials=N (grouped by
                sim.trial) at TAU2_MAX_CONCURRENCY=125, and return {task_id: [trial0, trial1, ...]}
                (len n_trials, trial-ordered). When present, cap-evolve calls it ONCE per candidate
                instead of looping run_batch per trial; per-trial persistence
                (rollouts/<split>/<task>__<tag>__t<k>.json) is UNCHANGED so pass^k / SE / resume
                keep working. This collapses N sequential eval passes into one batched run.
- agent AND user simulator:  openai/gpt-oss-120b  via IBM RITS
- RITS wiring:  litellm model "hosted_vllm/openai/gpt-oss-120b" + per-call api_base +
                extra_headers {"RITS_API_KEY": ...}  (NO litellm monkeypatch, NO tau2 fork)
- credentials:  RITS_API_KEY (+ RITS_API_URL) in the repo-root .env
- concurrency:  TAU2_MAX_CONCURRENCY=125

# 4. SCORER  (what to optimize against) — and WHERE the metric comes from
- metric:       tau2's own task reward in [0,1] (required actions performed + info communicated)
- metric source: tau2 computes it per simulation as `sim.reward_info.reward`; the per-check
                breakdown is in `sim.reward_info` (db_check / action_checks / communicate_checks /
                nl_assertions / env_assertions). Implement adapter.score() to read the reward +
                reward_info that run_batch stashes from each simulation, and verify score() is
                deterministic on a fixed rollout (the `cap-evolve check` gate enforces this).
- feedback:     gold-AWARE but gold-SAFE — which required actions/info were missed (the learning
                signal), derived from reward_info checks; never leak the gold answer.
- objective:    maximize mean reward on the VAL split

# 4b. TRAJECTORIES  (the FULL traces the optimizer reads) — PATH IS AN INPUT
- where:        tau2's batch runner can persist its native per-task simulation results
                (full message transcript + reward_info) to a directory via run_tasks(save_path=...).
                Point run_batch's save_path at a per-eval dir UNDER THE RUN, e.g.
                <run_dir>/trajectories/val/  (any structure/format tau2 writes is fine).
- expose:       implement adapter.trajectories(split) to return that directory. cap-evolve copies
                it VERBATIM into the optimizer's working dir as ./trajectories/ each iteration, so
                the optimizer reads the complete, unmodified traces (not a lossy summary).
                (If you cannot persist native files, return None — cap-evolve falls back to copying
                its own per-rollout JSON, which already embeds each rollout's full message trace.)

# 5. OPTIMIZER  (proposes the edits) + MODEL + CREDENTIALS + CONTEXT
- optimizer:    claude-code
- model:        claude-opus-4-6
- credentials:  a logged-in Claude Code session (or ANTHROPIC_API_KEY)
- runner_repo_path:  ../tau2-bench  (the cloned checkout — surfaced to the optimizer as
                read-only context so it can consult tau2's tools/scoring/task structure)
- optimizer instructions: author .capevolve/project/optimizer/INSTRUCTIONS.md from the scaffolded
                template (keep its {{...}} placeholders intact — the harness fills them per
                iteration), tailoring the guidance + the "READ THESE" pointers (./trajectories/,
                ./guidance/<cap>/, ./guidance/diagnose/SKILL.md, ./guidance/optimizer/claude-code.md,
                ./STATE.md, ./MEMORY.md, ../tau2-bench) to this benchmark. The authored INSTRUCTIONS
                must follow the new flow: READ ./MEMORY.md FIRST and never re-propose a rejected
                approach; address ALL failure clusters each iteration (fan out one subagent per
                cluster, each in its own worktree, then merge all edits into ONE candidate); and end
                STATE.md with the rich "## Handover for next iteration" section (approaches tried,
                lessons, recommendation, what NOT to retry). For the tools capability, the primary
                edit is CODE-BEARING tools — a validation tool that enforces a rule in code then
                calls the existing tool and removes the raw one; a workflow tool that collapses a
                recurring sequence; and a composite WRITE tool that performs a stalled multi-step
                action in code (then removes the raw write primitives) so the agent cannot analyze,
                confirm, and then fail to execute — not docstring prose.

# 6. BUDGET / GATE
- algorithm:        hill-climb  (--focus all)
- max_iterations:   10          num_trials: 10
- per-iteration optimizer $ cap:  optimizer_usd_per_iter 40   (claude --max-budget-usd, enforced by the CLI itself)
- optimizer_max_turns: 400      (generous; the $ cap is the real per-iteration ceiling)
- max_usd: 400      max_optimizer_usd: 400
- gate:             significant (paired), k_se 0.2
- store:            git          (every iteration committed for an inspectable process)
```

> The bundled `examples/tau2_airline/` is the **result** of following this prompt:
> the adapter (`adapters/adapter.py`), the RITS shim (`adapters/rits.py`), the seed
> capability (`seed_capability/`), and the optimizer instructions
> (`.capevolve/project/optimizer/INSTRUCTIONS.md`) are what the intake/implement-and-check
> flow produced — including `adapter.trajectories()` (native tau2 traces) and `score()`
> (reads `reward_info`). `setup.sh` is the executable transcript of that onboarding
> (clone+install tau2, scaffold via intake, wire the adapter + trajectories + scoring,
> `cap-evolve check`); `run.sh` runs the full optimization with the live dashboard. See `DEMO.md`.
