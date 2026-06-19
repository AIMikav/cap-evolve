<p align="center">
  <img src="docs/assets/cap-evolve-logo.png" alt="cap-evolve" width="200"/>
</p>

<h1 align="center">cap-evolve</h1>

<p align="center"><em>watch capability evolve</em></p>

<!-- badges: replace OWNER/REPO once published -->
![status](https://img.shields.io/badge/status-beta%20(0.x)-orange)
![tests](https://img.shields.io/badge/tests-28%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![deps](https://img.shields.io/badge/runtime%20deps-0%20(stdlib)-success)
![license](https://img.shields.io/badge/license-MIT-informational)
![skills](https://img.shields.io/badge/agent%20skills-18-7c5cff)

**Optimize any AI agent's capabilities — its skills, tools/MCP, and prompts —
against your own eval. Host-agnostic. Honest train/val/test. Every iteration
versioned in git.**

cap-evolve is a library of [Agent Skills](https://www.anthropic.com/news/skills)
(plus a tiny stdlib core) that turns "make this agent better at X" into a
disciplined loop *any* coding agent can run: collect inputs → wire a small
adapter → evaluate → diagnose failures → propose edits → keep only what beats a
held-out set → report. It optimizes what your agent *reads*, and reports a single,
honest number you can trust.

> **Status:** beta (`0.x`) — APIs may change. Working end to end; proven on a real
> benchmark (see [Results](#results)).

**Contents:** [Quickstart](#quickstart-60-seconds) ·
[Optimize your own](#optimize-your-own-skill-tool-or-agent) · [Results](#results) ·
[Supported agent hosts](#supported-agent-hosts) · [Install](#install) ·
[Usage](#usage-swap-one-word) · [How it works](#how-it-works) ·
[Dashboard](#dashboard) · [Comparison](#how-it-compares) ·
[Skill library](#skill-library) · [Examples](#examples) ·
[Extending](#extending) · [Contributing](#contributing) · [Citation](#citation)

## Quickstart (60 seconds)

**Prerequisites:** Python 3.10+ and git — that's all for this example (it's
**zero-API**, so no model key needed). A *real* optimization additionally needs a
coding-agent CLI to act as the optimizer (e.g. `claude`, `codex`, `gemini`) plus
its API key — see [Optimize your own](#optimize-your-own-skill-tool-or-agent).

**Step 1 — verify the install with a real, zero-API run** (the `toy_calc` example:
a deterministic agent whose score depends on its system prompt; the `mock` optimizer
edits the prompt, so no API is called):

```bash
git clone <repo> cap-evolve && cd cap-evolve
pip install ./core                         # the honest-eval substrate (CLI: cap-evolve)
./install.sh                               # place skills into your agent host
bash examples/toy_calc/run.sh              # scaffold a tmp project dir and run end-to-end
```
```jsonc
{
  "baseline_val": 0.0,        // seed prompt fails every task
  "test_reward": 1.0,         // optimized prompt, scored ONCE on the sealed test split
  "test_pass_k": {"1": 1.0},
  "dashboard": ".capevolve/run_*/dashboard.html"   // open in any browser
}
```
Open the printed `dashboard.html` to see the run. That confirms the install works.

**Step 2 — optimize your own skill / tool / agent:** see the next section.
Or, host-agnostic: point any agent at [`RUN.md`](RUN.md) and say *"follow RUN.md."*

## Optimize your own skill, tool, or agent

The Quickstart runs a *bundled* example. To optimize **your** capability against
**your** benchmark, you supply three things and cap-evolve runs the loop:

1. **The capability to optimize** — a skill (`SKILL.md` package), a tool's code, an
   MCP tool definition, or a system prompt. A *copy* is edited each iteration; your
   original is never touched.
2. **Tasks** — your benchmark's eval cases (each with an id + a gold/criterion).
3. **A scorer** — how one run becomes a reward in `[0,1]` (+ short feedback).

You connect these once through a tiny **adapter** (`tasks · run_target · score`
plus a pure `materialize` + a `live` context manager; `apply` is kept as a
back-compat hook). There are two ways to get there — pick one:

### Path A — let your coding agent build and run it (no Python from you)
Open the coding agent you already use (**Claude Code**, Codex, Gemini CLI, opencode,
…) at the repo root and tell it to follow `RUN.md`. It loads the `intake` skill, asks
you for anything missing, **writes the adapter for you**, runs the `cap-evolve check` gate,
then the full optimize → significance-gate → sealed-test → report loop, and prints the
dashboard path. (This is exactly how [`examples/tau2_airline`](examples/tau2_airline)
was onboarded — paste [`examples/tau2_airline/PROMPT.md`](examples/tau2_airline/PROMPT.md)
and the agent clones + installs the benchmark, writes the adapter, and runs the loop.)

Give it the details `intake` needs up front (anything you omit, it will ask for — and
will **never fabricate a NEEDED input**). Copy this template and fill it in:

```text
Follow RUN.md to run a cap-evolve optimization. Here is everything intake needs:

# 1. CAPABILITY TO OPTIMIZE  (what gets edited each iteration)
- type:            system-prompt | tools | mcp-tool | skill-package   (one or a list)
- local path:      <path to the skill dir / tool file / prompt / policy to optimize>

# 2. BENCHMARK / DATASET  (the eval)
- benchmark repo:  <local path or git URL of the benchmark, e.g. ./my-bench>
- tasks:           <tasks.jsonl path>  OR  "adapter" (the adapter builds them from the benchmark)
- task format:     each case has an id, an input, and a gold/criterion
- splits:          ratio 0.5/0.25/0.25 (seeded)  |  explicit ids file  |  all-in-each (no holdout, fit metric)

# 3. RUNNER  (the agent under test) + MODELS + CREDENTIALS
- how to run one task: <your agent's CLI/SDK/HTTP entrypoint, or the benchmark's own batch runner>
- runner model(s): <e.g. watsonx/openai/gpt-oss-120b>
- credentials:     <env vars / .env keys the runner needs, e.g. RITS_API_KEY, WATSONX_*>

# 4. SCORER  (what to optimize against)
- metric:          <exact-match | task reward in [0,1] | rubric | a pass/fail rule>
- where it comes from: <the benchmark's verifier, or your scoring function>
- objective:       maximize mean reward on the VAL split

# 5. OPTIMIZER  (proposes the edits) + MODEL + CREDENTIALS
- optimizer:       claude-code | codex | gemini-cli | opencode | openclaw | ibm-bob | generic | mock
                   (one runner — `run-optimizer` — resolves the name via optimizers/registry.yaml)
- optimizer model: <e.g. claude-opus-4-6>   (omit to use the optimizer's default)
- credentials:     <e.g. ANTHROPIC_API_KEY, or BOBSHELL_API_KEY for ibm-bob>

# 6. BUDGET / GATE
- algorithm:       hill-climb (--focus all|cyclic|hardest-first) | gepa | skillopt
                   (gepa & skillopt are the sample-efficient flagships)
- max_iterations:  <N>     num_trials: <K, use >=3 for a stochastic agent>
- max_metric_calls:<N, 0=unlimited>   max_usd: <$, 0=unlimited; runner+optimizer+intake>
- max_optimizer_usd:<$, 0=off>        optimizer_max_turns: <N, per-iteration agent-CLI cap>
- gate:            significant (k_se, e.g. 1.0) | strict | threshold   (paired sig is the default)
```

**Preview the spend before you run.** `cap-evolve estimate --spec capevolve.yaml`
prints the call counts (`val × trials × iterations` runner calls, `iterations`
optimizer calls) and a $ range — calibrated from your prior runs' *actual* reported
cost when available, else priced from a bundled table (or `--price-in/--price-out`).
Every cap is a hard stop, and soft `budget_warning` events fire at 50%/80% of
`max_usd`. Optimizer spend is tracked per role (intake / optimizer / runner) and
counts toward `max_usd`. Any cap can also be overridden at the command line:

```bash
cap-evolve estimate --spec capevolve.yaml          # dry-run cost preview (spends nothing)
cap-evolve run --spec capevolve.yaml --max-usd 10 --max-iterations 5 \
               --optimizer-max-turns 30            # claude-code → --max-turns 30 per step
```

**Worked example — tau2-bench airline, onboarded from a single prompt.** The bundled
[`examples/tau2_airline`](examples/tau2_airline) shows cap-evolve taking a **brand-new
benchmark** from one paste-the-prompt to an honest, optimized result. You don't pre-install
anything: intake **clones + installs tau2-bench**, wires IBM RITS, writes the adapter,
runs the `cap-evolve check` gate, then the full loop with a **live capybara dashboard**.

- **Paste the prompt:** [`examples/tau2_airline/PROMPT.md`](examples/tau2_airline/PROMPT.md)
  — give it to Claude Code at the repo root and say *"follow RUN.md."* It is the exact
  intake input: capability `[system-prompt, tools]` (airline **policy + tools**, jointly),
  benchmark tau2-bench airline (git URL + `pip install -e`), runner `openai/gpt-oss-120b`
  via RITS as **both agent and user simulator**, scorer = tau2's task reward, optimizer
  `claude-code @ claude-opus-4-6`, hill-climb, all 50 tasks, 10 trials.
- **Or run the executable transcript** of that onboarding directly (two commands):

```bash
# RITS creds in repo-root .env (RITS_API_KEY, RITS_API_URL); be logged into Claude Code
bash examples/tau2_airline/setup.sh   # intake onboarding: install cap-evolve + clone/install
                                      # tau2-bench (records the commit) + scaffold project
                                      # + wire the adapter/RITS shim/seed + cap-evolve check (hard gate)
bash examples/tau2_airline/run.sh     # cap-evolve run --dashboard auto: full run + live capybara dashboard
```

Key facts of this run: the RITS runner uses `gpt-oss-120b` as **both** agent and user
simulator; the optimizer is `claude-opus-4-6`; each iteration spends under a per-iteration
`$` cap (`--max-budget-usd`, enforced by the Claude CLI itself); acceptance is the **paired
significance gate**; every iteration is a **git commit**; and the **live dashboard** shows
per-iteration optimizer + runner **cost & time** plus the one-time **intake cost**. Full
walkthrough: [`examples/tau2_airline/DEMO.md`](examples/tau2_airline/DEMO.md). Reproduce
from zero: [docs/REPRODUCE_tau2.md](docs/REPRODUCE_tau2.md).

### Path B — drive it yourself with the `cap-evolve` CLI
```bash
# 1. scaffold a project (adapter STUB + capevolve.yaml + PROJECT.md)
python3 skills/phases/intake/scripts/run.py --base .capevolve

# 2. implement the methods in .capevolve/project/adapters/adapter.py:
#      tasks(split)                 -> your benchmark's tasks  (id, input, target)
#      run_target(task, ctx, *,seed)-> run YOUR agent on the task with the candidate live as ctx;
#                                      forward `seed` to a stochastic runner so trials vary (real pass^k)
#      score(task, rollout)         -> reward in [0,1] + feedback
#      materialize(cand_dir, edits) -> PURE write of edits into cand_dir (no global effect)
#      live(cand_dir)               -> context manager: make cand_dir live for ONE eval, yields ctx
#    (apply(cand_dir, edits) is still supported as a back-compat hook; the default live() calls it.)
#    Fastest path: copy the closest example adapter below and edit it.

# 3. fill .capevolve/project/capevolve.yaml  (capabilities / optimizer / algorithm / splits)

# 4. hard gate, then run
cap-evolve check .capevolve/project
cap-evolve run --spec .capevolve/project/capevolve.yaml --project .capevolve/project
open .capevolve/run_*/dashboard.html
```

**Start from the closest worked example** — copy its `adapter.py`, point it at your
data, swap `capabilities` in `capevolve.yaml`:

| You want to optimize… | Copy this example | `capabilities:` |
|---|---|---|
| a simple **prompt** (zero-API proof) | [`examples/toy_calc`](examples/toy_calc) | `[system-prompt]` |
| a **system prompt + tools** (real agent) | [`examples/tau2_airline`](examples/tau2_airline) | `[system-prompt, tools]` |

### Pointing it at your own benchmark
Your benchmark plugs in **only** through the adapter — nothing else changes:
- `tasks(split)` reads your benchmark's cases (its files, or an API call).
- `run_target(task, ctx, *, seed)` runs your agent on one task **with the candidate
  capability live** (`ctx` is what `live()` yielded), capturing output/trace into a
  `Rollout`; forward `seed` if the runner is stochastic, and set `Rollout.error` when
  a run fails for an infrastructure reason so the gate treats it as noise.
- `score(task, rollout)` turns that into a reward using your benchmark's metric.

If your benchmark ships its **own batch runner**, implement `run_batch` instead of
per-task `run_target` (see [`examples/tau2_airline/adapter.py`](examples/tau2_airline/adapter.py))
so cap-evolve drives the benchmark's runner directly. Splits, trials, the gate,
pass^k, the sealed test, and the dashboard are all handled for you.

## Results

Real [tau2-bench](https://github.com/sierra-research/tau2-bench) **airline** run —
optimizing the airline **policy + tools together** with a `claude-opus-4-6` optimizer and
`gpt-oss-120b` as both agent and user simulator (via IBM RITS), over all 50 tasks:

<!-- RESULTS_PLACEHOLDER: baseline/best/test + pass^k to be filled from the latest run -->

> Numbers come from the latest run in [`examples/tau2_airline/run_full/`](examples/tau2_airline/run_full/)
> (`report.md` / `dashboard.html`); every iteration is a git commit. Reproduce from
> zero: [docs/REPRODUCE_tau2.md](docs/REPRODUCE_tau2.md).

## Supported agent hosts

Any of these can drive the **optimizer** (the agent that proposes edits). One skill
(`run-optimizer`) resolves the optimizer name to a verified headless command via
`optimizers/registry.yaml` (one row per optimizer); per-CLI install/auth prose lives
in `skills/optimizers/run-optimizer/references/<name>.md`. Adding an optimizer is one
YAML row — no new skill.

| Host (optimizer) | Registry name | Headless command | Status |
|---|---|---|---|
| Claude Code | `claude-code` | `claude -p … --permission-mode acceptEdits` | stable |
| OpenAI Codex CLI | `codex` | `codex exec --sandbox workspace-write …` | stable |
| Gemini CLI | `gemini-cli` | `gemini -p … --approval-mode=yolo` | stable |
| opencode | `opencode` | `opencode run --dangerously-skip-permissions …` | stable |
| OpenClaw | `openclaw` | configurable (`CAPEVOLVE_OPENCLAW_CMD`) | beta |
| IBM Bob | `ibm-bob` | `bob --accept-license --yolo --chat-mode code …` | beta |
| Any CLI agent | `generic` | `CAPEVOLVE_OPTIMIZER_CMD` template | stable |
| (tests / CI) | `mock` | deterministic, zero-API | stable |

`install.sh` auto-detects each host's skill dir (Claude Code `.claude/skills`,
Codex `.agents/skills`, opencode native, Gemini extensions, …).

## Install
```bash
pip install ./core            # package: cap-evolve-core, CLI: cap-evolve
./install.sh                  # copy skills into your host's skills dir (optionally --host <name>)
```

**Claude Code plugin (one command):** `claude --plugin-dir ./plugins/cap-evolve`
exposes every skill as `/cap-evolve:<skill>` (each phase is dual-mode: standalone
slash command **+** orchestrator-callable **+** headless JSON), ships honesty **hooks**
(PreToolUse denies edits to the sealed test split / gold; Stop/SubagentStop blocks
finishing until `cap-evolve check` / the gate is green), a read-only diagnoser and a
writer proposer subagent, and a `using-cap-evolve` session-start router. The honesty
enforcement lives in **core-owned scripts**, never in editable skill markdown.

## Usage (swap one word)

Name the optimizer in `capevolve.yaml`; the loop is identical — only the optimizer
NAME changes (one runner, `run-optimizer`, resolves it via `optimizers/registry.yaml`):

```yaml
# .capevolve/project/capevolve.yaml
capabilities: [system-prompt, tools]   # list of capabilities to optimize jointly
                                       #   any of: system-prompt | tools | mcp-tool | skill-package
optimizer:        claude-code      # ← swap the NAME: codex | gemini-cli | opencode | openclaw | ibm-bob | generic | mock
algorithm_skill:  hill-climb       # hill-climb (--focus all|cyclic|hardest-first) | gepa | skillopt
num_trials: 4
store: git                         # versions every iteration; or: copy | command (e.g. a skills store)
```
```bash
python3 -m cap_evolve.cli run --spec .capevolve/project/capevolve.yaml --project .capevolve/project
```

## How it works
1. **Intake** — interview the user, scaffold `.capevolve/project/`, gather inputs (ask if missing).
2. **Implement & check** — fill the adapter (`tasks · run_target · score` + `materialize`/`live`; `apply` back-compat); `cap-evolve check` is a hard gate.
3. **Baseline** — freeze seeded train/val/test (test **sealed**), score the seed on val.
4. **Optimize** — each iteration: diagnose failing traces → the optimizer edits a candidate → score on **val** (each trial gets its own `seed`, so pass^k measures real variance) → a **paired significance gate** (Δ > k·SE, auto-selected because candidate & current share val tasks) accepts or rejects → commit to git, update memory.
5. **Finalize** — score the best candidate on the **sealed test split, exactly once** (**seal-on-success**: a finalize crash never burns the headline number).
6. **Report** — `report.md` + a self-contained `dashboard.html`.

Honesty is enforced in code, not docs: the only place rewards are aggregated,
splits are made, the gate is applied, and test is sealed is `cap_evolve` —
see [docs/HONEST_EVAL.md](docs/HONEST_EVAL.md). Infra-vs-capability failures are
distinguished by a structured `Rollout.error` signal, not by substring-matching
feedback prose. You implement the adapter once; everything else is provided
([docs/ADAPTER_CONTRACT.md](docs/ADAPTER_CONTRACT.md)).

## Dashboard
Every run writes a **self-contained** `dashboard.html` (run data inlined, no CDN —
opens offline): a KPI strip (best / baseline / %Δ / counts by status / frontier /
epoch), a **cumulative-best stair** over the per-iteration score scatter, a
**tasks × iterations pass/fail heatmap** (surfaces the regressions and specialists
the mean hides), a per-iteration **diff** view, a **lineage tree** (parents →
children; merges show as multi-parent), **optimizer-vs-runner cost / tokens /
latency**, and an annotations/diagnoses stream. For in-chat progress, run
`cap-evolve report --terminal` for an ANSI report (CLAUDECODE margin-aware).

## How it compares

| | cap-evolve | DSPy | GEPA | promptfoo |
|---|:--:|:--:|:--:|:--:|
| Optimizes prompts | ✅ | ✅ | ✅ | ❌ (eval only) |
| Optimizes tools/MCP + skills | ✅ | ➖ | ➖ | ❌ |
| **Sealed test + significance gate enforced in code** | ✅ | ➖ | ➖ | ➖ |
| pass^k *and* pass@k + bootstrap CI | ✅ | ❌ | ❌ | ❌ |
| Reflective Pareto evolution (GEPA) | ✅ | ✅ | ✅ | — |
| **Runs on any agent host (no framework)** | ✅ | ❌ | ❌ | ➖ |
| Git-versioned iterations + optimizer memory | ✅ | ❌ | ❌ | ❌ |
| Zero runtime dependencies | ✅ | ❌ | ❌ | ❌ |

Whitespace: **skills-native + host-agnostic + honesty enforced in code.** Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md).

## Skill library

**18 skills.** The library was deliberately collapsed: the 8 per-CLI optimizer
skills became **one** `run-optimizer` skill + a one-row-per-optimizer
`optimizers/registry.yaml`, and the three hill-climb clones became **one**
`hill-climb` skill with `--focus`.

| Component | Skills |
|-----------|--------|
| orchestrate | `orchestrate` · `using-cap-evolve` (session-start router) |
| phases | `intake` · `implement-and-check` · `baseline` · `evaluate` · `diagnose` · `gate` · `finalize` · `report` |
| capabilities | `system-prompt` · `skill-package` · `tools` · `mcp-tool` |
| algorithms | `hill-climb` (`--focus all\|cyclic\|hardest-first`) · `gepa` · `skillopt` |
| optimizers | `run-optimizer` + `optimizers/registry.yaml` (`claude-code`, `codex`, `gemini-cli`, `opencode`, `openclaw`, `ibm-bob`, `generic`, `mock`) |

`gepa` (real GEPA — two-stage minibatch-then-full-val economy, per-instance Pareto
frontier, reflective dataset, system-aware merge; arXiv:2507.19457) and `skillopt`
(epochs × mini-batches, decaying textual-LR edit budget, rejected-edit buffer, gated
slow update; arXiv:2605.23904) are the sample-efficient **flagships**; `hill-climb`
(`--focus all|cyclic|hardest-first`) is the simple global-best baseline climber.

## Examples

| Example | What it is |
|---|---|
| [`examples/toy_calc`](examples/toy_calc) | zero-API deterministic proof — the CI gate (no model key needed). |
| [`examples/tau2_airline`](examples/tau2_airline) | the real tau2-bench airline run, onboarded from a single prompt ([`PROMPT.md`](examples/tau2_airline/PROMPT.md) → [`setup.sh`](examples/tau2_airline/setup.sh) → [`run.sh`](examples/tau2_airline/run.sh); see [`DEMO.md`](examples/tau2_airline/DEMO.md)). |

## Extending
A new capability / algorithm / optimizer is **one folder** — clone `templates/skill`,
fill `meta.yaml`, drop it in; the registry auto-discovers it by `needs`/`provides`.
See [docs/EXTENDING.md](docs/EXTENDING.md).

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).
Report security issues via [SECURITY.md](SECURITY.md). Changes: [CHANGELOG.md](CHANGELOG.md).

## Citation
```bibtex
@software{cap-evolve,
  title  = {cap-evolve: a skills-native, host-agnostic harness for honestly
            optimizing AI-agent capabilities},
  year   = {2026},
  note   = {https://github.com/skillberry-ai/cap-evolve}
}
```
**Acknowledgements.** The `gepa` and `skillopt` algorithm skills are independent
implementations of the methods described in the GEPA (arXiv:2507.19457) and SkillOpt
(arXiv:2605.23904) papers — no third-party code is included; both reference projects
are MIT-licensed. cap-evolve also draws on ideas from DSPy, tau-bench/tau2-bench, and
the Agent Skills standard. Full citations: [docs/sources.bib](docs/sources.bib).

## License
MIT.
