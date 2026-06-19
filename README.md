<p align="center">
  <img src="docs/assets/cap-evolve-logo.png" alt="cap-evolve" width="200"/>
</p>

<h1 align="center">cap-evolve</h1>

<p align="center"><em>watch capability evolve</em></p>

<p align="center">
  <img src="https://img.shields.io/badge/status-beta%20(0.x)-orange" alt="status">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/runtime%20deps-0%20(stdlib)-success" alt="deps">
  <img src="https://img.shields.io/badge/license-MIT-informational" alt="license">
  <img src="https://img.shields.io/badge/agent%20skills-18-7c5cff" alt="skills">
</p>

**cap-evolve is a skills-based, host-agnostic harness that optimizes *any* agent
capability — a system prompt, its tools/MCP, or a whole skill package — against
*your* eval, with honesty enforced in code and every iteration git-versioned.**

You wire a tiny adapter once (or let a coding agent write it for you). cap-evolve
runs the loop: evaluate → diagnose failures → propose an edit → keep it only if it
beats a held-out split by a significant margin → commit → report a single honest
number. It optimizes what your agent *reads*, not its weights.

**Contents:** [Why](#why-cap-evolve) · [Install](#install) ·
[Toy example](#toy-example-zero-api) · [tau2-bench example](#tau2-bench-example-real) ·
[Optimize your own](#optimize-your-own) · [How it works](#how-it-works) ·
[Comparison](#how-it-compares) · [Skill library](#skill-library) ·
[Results](#results) · [License](#license)

## Why cap-evolve

- **Optimizes prompts, tools/MCP, *and* skill packages** — not just prompts.
  Pick one or several (`[system-prompt, tools, mcp-tool, skill-package]`) and
  optimize them jointly.
- **Onboard any benchmark/agent from a single prompt.** Paste one intake brief to
  your coding agent; it installs the benchmark, wires a tiny adapter, and runs the
  loop. No pre-integration.
- **Honesty enforced in code, not docs.** The sealed test split is scored exactly
  once and a paired significance gate (Δ > k·SE) decides every acceptance — both
  live in the `cap_evolve` core, the only place rewards are aggregated.
- **Host- and agent-agnostic.** The optimizer is *any* coding-agent CLI
  (claude-code, codex, gemini, opencode, ibm-bob, …) resolved by one registry row.
  No framework lock-in.
- **Git-versioned iterations + optimizer memory** — every candidate is a commit;
  rejected approaches are remembered and never re-proposed.
- **Per-iteration optimizer $ budget**, enforced by the optimizer CLI itself
  (e.g. claude `--max-budget-usd`), plus hard total caps and a dry-run estimate.
- **Live dashboard** — per-iteration optimizer & runner cost + time, intake cost,
  lineage tree, per-iteration diffs, and a tasks × iterations pass/fail heatmap.
- **Skills-native & trivially extensible** — a new capability, algorithm, or
  optimizer is one folder or one registry row.
- **Zero runtime dependencies** — the core is pure Python stdlib.

## Install

Requires **Python 3.10+** and **git**.

```bash
git clone <repo> cap-evolve && cd cap-evolve
python3 -m venv .venv && source .venv/bin/activate   # recommended (isolated env)
pip install ./core                  # the honest-eval core (package: cap-evolve-core, CLI: cap-evolve)
pip install ./dashboard/backend     # optional: the live dashboard UI (cap-evolve run --dashboard auto)
./install.sh                        # optional: copy skills into your agent host's skills dir
cap-evolve version                  # verify the install
```

> **If your default pip index requires auth**, append `--index-url https://pypi.org/simple`
> to the `pip install` lines (cap-evolve-core itself has zero runtime deps).

Optimizing a real agent additionally needs: a coding-agent CLI to act as the
**optimizer** (e.g. `claude`, `codex`, `gemini`) with its credentials, and your
**runner**'s model credentials — all in a repo-root `.env` (e.g. `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `RITS_API_KEY`, `WATSONX_*`). The toy example below needs **none** of this.

## Toy example (zero-API)

Verify the install with a deterministic, no-key run. `toy_calc` is a stand-in
agent that only answers correctly when its system prompt contains a `[CALC]`
marker; the `mock` optimizer adds it, so the score provably rises — no model is
called.

```bash
bash examples/toy_calc/run.sh
```

Expected: the seed prompt scores `0.0` on val; the optimized prompt is gate-accepted
and scores `1.0` on the sealed test split.

```text
baseline_val 0.0  ->  test_reward 1.0   (gate-accepted, test sealed) + dashboard.html
```

This is exactly what `core/tests/test_e2e_slice.py` asserts. Open the printed
`dashboard.html` in any browser to see the run.

## tau2-bench example (real)

The bundled [`examples/tau2_airline`](examples/tau2_airline) takes a **brand-new
benchmark** from one prompt to an honest, optimized result. It optimizes the
airline **policy + tools together** with a `claude-opus-4-6` optimizer, using
`gpt-oss-120b` over IBM RITS as **both** the agent and the user simulator.

```bash
# RITS creds in repo-root .env (RITS_API_KEY, RITS_API_URL); be logged into Claude Code.
bash examples/tau2_airline/setup.sh   # intake: clone + pip install -e tau2-bench, scaffold
                                      # the project, wire adapter + RITS shim + seed,
                                      # then cap-evolve check (the hard gate)
bash examples/tau2_airline/run.sh     # cap-evolve run --dashboard auto: full loop + live UI
```

This two-command path is simply the executable transcript of pasting
[`PROMPT.md`](examples/tau2_airline/PROMPT.md) to your coding agent and saying
*"follow [`RUN.md`](RUN.md)."* Intake onboards tau2-bench (recording the resolved
commit), wires the adapter, passes `cap-evolve check`, then optimizes over all 50
airline tasks (10 trials each) under a per-iteration `--max-budget-usd` cap, with a
paired significance gate and a git commit per iteration. `--dashboard auto` serves
the live capybara UI; the `setup.sh` flag `--dashboard` / `--no-dashboard` toggles
installing that server. Full walkthrough: [`DEMO.md`](examples/tau2_airline/DEMO.md);
reproduce from zero: [`docs/REPRODUCE_tau2.md`](docs/REPRODUCE_tau2.md).

This is the exact prompt that produced this example — paste it to your coding agent
and say *"follow RUN.md"*:

```text
Follow RUN.md to run a cap-evolve optimization. Onboard this as a brand-new
benchmark — the intake/integration step should CLONE + INSTALL it (not assume it
exists). Here is everything intake needs:

# 1. CAPABILITY TO OPTIMIZE  (a copy is edited each iteration; the original is never touched)
- type:         [system-prompt, tools]      # the airline POLICY and the TOOLS, jointly
- tools means:  edit tool docstrings/descriptions; edit tool behavior/code; and
                ADD/REMOVE tools, including composite tools that call existing tools
- seed:         tau2-bench's canonical airline policy + its airline tool set

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
- agent AND user simulator:  openai/gpt-oss-120b  via IBM RITS
- RITS wiring:  litellm model "hosted_vllm/openai/gpt-oss-120b" + per-call api_base +
                extra_headers {"RITS_API_KEY": ...}  (NO litellm monkeypatch, NO tau2 fork)
- credentials:  RITS_API_KEY (+ RITS_API_URL) in the repo-root .env
- concurrency:  TAU2_MAX_CONCURRENCY=100

# 4. SCORER  (what to optimize against)
- metric:       tau2's own task reward in [0,1] (required actions performed + info communicated)
- feedback:     gold-AWARE but gold-SAFE — which required actions/info were missed (the learning signal)
- objective:    maximize mean reward on the VAL split

# 5. OPTIMIZER  (proposes the edits) + MODEL + CREDENTIALS
- optimizer:    claude-code
- model:        claude-opus-4-6
- credentials:  a logged-in Claude Code session (or ANTHROPIC_API_KEY)

# 6. BUDGET / GATE
- algorithm:        hill-climb  (--focus all)
- max_iterations:   10          num_trials: 10
- per-iteration optimizer $ cap:  optimizer_usd_per_iter 40   (claude --max-budget-usd, enforced by the CLI itself)
- optimizer_max_turns: 400      (generous; the $ cap is the real per-iteration ceiling)
- max_usd: 400      max_optimizer_usd: 400
- gate:             significant (paired), k_se 0.2
- store:            git          (every iteration committed for an inspectable process)
```

## Optimize your own

To optimize **your** capability against **your** benchmark, you wire one small
**adapter** ([`docs/ADAPTER_CONTRACT.md`](docs/ADAPTER_CONTRACT.md)) — three
required methods plus optional hooks:

```python
tasks(split)                   -> list[Task]   # your eval cases for 'train'|'val'|'test'|'all'
run_target(task, ctx, *, seed) -> Rollout      # run your agent with the candidate LIVE as ctx;
                                               #   forward `seed` if stochastic; set Rollout.error on infra failure
score(task, rollout)           -> Score        # reward in [0,1] + feedback (never leak the gold)

# optional (working defaults provided):
materialize(cand_dir, edits)   -> None         # PURE write of edits into cand_dir
live(cand_dir)                 -> ctx (CM)      # make the candidate live for ONE eval
run_batch(tasks, ctx, *, seed) -> ...           # implement INSTEAD of run_target to drive a
                                               #   benchmark's OWN batch runner (as tau2 does)
```

Everything else — splits, trials, gating, pass^k, the sealed test, memory, and the
dashboard — is provided by the core and must not be reimplemented (that is what
keeps eval honest). Two ways to get there:

**A — let your coding agent build it (no Python from you).** Open the coding agent
you already use at the repo root and tell it to follow `RUN.md`. It loads the
`intake` skill, asks for anything missing (never fabricating a NEEDED input),
writes the adapter, runs `cap-evolve check`, then the full loop.
[`examples/tau2_airline/PROMPT.md`](examples/tau2_airline/PROMPT.md) is a complete
worked brief (also embedded verbatim in the
[tau2-bench section](#tau2-bench-example-real) above).

Fill this in and paste it to your coding agent with *"follow RUN.md"* — intake asks
for anything you omit and never fabricates a needed input:

```text
Follow RUN.md to run a cap-evolve optimization on MY benchmark/agent. If the
benchmark is not installed yet, the intake/integration step should CLONE + INSTALL
it. Here is everything intake needs (fill each field; leave a field blank only if
you want intake to ask):

# 1. CAPABILITY  (what gets optimized — a COPY is edited each iteration; the original is never touched)
- type:    <one or a list of: system-prompt | tools | mcp-tool | skill-package>
           # system-prompt = a prompt/policy text file; tools = the agent's OWN tools;
           # mcp-tool = tools served by an EXTERNAL MCP server (only docs/exposed-set edits);
           # skill-package = an Agent Skill dir (SKILL.md + refs + scripts). Combine, e.g. [system-prompt, tools].
- seed:    <path to the seed artifact to optimize, e.g. policy/policy.md | tools.json | skills/<name>/>
- NOTE for `tools`: the optimizer may edit tool docstrings/descriptions AND tool
           behavior/code, AND add/remove COMPOSITE tools that call existing tools
           (wrapping rules, loops, argument normalization) — not just reword docs.

# 2. BENCHMARK / DATASET  (the eval)
- benchmark:  <name, e.g. my-bench / SWE-bench-lite / a homegrown suite>
- repo:       <local path OR git URL>            # where the benchmark code/data lives
- install:    <how to install it, e.g. `pip install -e ../<bench>`; RECORD the resolved commit for reproducibility>
- tasks:      <path to tasks.jsonl  OR  "adapter">   # "adapter" = adapter.tasks(split) builds them in-code
- task format: each task = id + input + gold/criterion
              # one JSON object per line: {"id": ..., "input": ..., "target"/"criterion": ...}
- splits:     <one of:>
              #  seeded ratio   -> split_seed + split_train/val/test (default 0.5/0.25/0.25)
              #  explicit       -> split_ids.json  {"train":[...],"val":[...],"test":[...]} (e.g. an official split)
              #  no-holdout fit -> train == val == test == all ids (report FLAGS the test number as a fit metric)

# 3. RUNNER  (the agent under test) + MODELS + CREDENTIALS
- how to run one task:  <in-process call | subprocess | HTTP endpoint
                         | the benchmark's OWN batch runner -> implement adapter.run_batch instead of run_target>
- runner model(s):      <model id(s) the agent under test uses>
- credentials:          <env vars / repo-root .env keys, e.g. OPENAI_API_KEY, WATSONX_*, RITS_API_KEY — never hardcode a secret>
- custom/OpenAI-compatible endpoint (vLLM, IBM RITS, a gateway):
                        <api_base + any custom auth header>
                        # pass via the runner's LLM config (most benchmarks forward extra kwargs to litellm);
                        # prefer PER-CALL config — no monkeypatch, no benchmark fork
- concurrency knob:     <e.g. an env var / max-concurrency setting the runner honors>

# 4. SCORER  (what to optimize against)
- metric:     <exact-match | reward in [0,1] | rubric | pass/fail rule>
- source:     <the benchmark's own verifier  OR  your score() function in adapter.py>
- feedback:   must be GENERAL and gold-SAFE — it is the learning signal; never leak the gold answer
- objective:  maximize mean reward on the VAL split

# 5. OPTIMIZER  (proposes the edits) + MODEL + CREDENTIALS
- optimizer:   <claude-code | codex | gemini-cli | opencode | openclaw | ibm-bob | generic | mock>
- model:       <backend-specific model id>
- credentials: <e.g. ANTHROPIC_API_KEY or a logged-in Claude Code session; BOBSHELL_API_KEY for ibm-bob>

# 6. BUDGET / GATE
- algorithm:            <hill-climb (--focus all|cyclic|hardest-first) | gepa | skillopt>
- max_iterations:       <N — dominant cost knob>
- num_trials:           <>=3 for a stochastic agent; 1 only for a deterministic one>   # enables pass^k
- max_metric_calls:     <0 = unlimited; else stop after N runner evals>
- max_usd:              <total $ cap over runner + optimizer + intake; 0 = unlimited>
- max_optimizer_usd:    <cumulative optimizer-only $ cap; 0 = unlimited>
- optimizer_usd_per_iter: <PER-ITERATION $ cap enforced by the optimizer CLI itself, e.g. claude `--max-budget-usd N`>
- optimizer_max_turns:  <per-iteration WORK cap passed to the agent CLI, e.g. claude `--max-turns N`>
- gate:                 <significant (k_se) | strict | threshold>
                        # significant: accept only if Δ > k_se · SE — k_se is how many standard errors
                        # the val gain must clear (e.g. 0.2 = lenient, 1.0 = strict) so noise isn't mistaken for progress
- stall:                <stop after N consecutive rejects; 0 = run all max_iterations>
- store:                git          # versions every iteration as a commit for an inspectable process
```

**B — drive the `cap-evolve` CLI yourself.**

```bash
python3 skills/phases/intake/scripts/run.py --base .capevolve   # scaffold adapter STUB + capevolve.yaml
# 1. implement tasks / run_target (or run_batch) / score in
#    .capevolve/project/adapters/adapter.py  (copy the closest example below)
# 2. set capabilities / optimizer / algorithm / splits in capevolve.yaml
cap-evolve check .capevolve/project                              # hard gate — must print {"ok": true}
cap-evolve estimate --spec .capevolve/project/capevolve.yaml     # dry-run cost preview (spends nothing)
cap-evolve run   --spec .capevolve/project/capevolve.yaml --project .capevolve/project
open .capevolve/run_*/dashboard.html
```

Start from the closest example and edit its `adapter.py`:

| You want to optimize…                       | Copy                                              | `capabilities:`          |
|---------------------------------------------|---------------------------------------------------|--------------------------|
| a **prompt** (zero-API proof)               | [`examples/toy_calc`](examples/toy_calc)          | `[system-prompt]`        |
| a **system prompt + tools** (real agent)    | [`examples/tau2_airline`](examples/tau2_airline)  | `[system-prompt, tools]` |

**Swapping the optimizer is one word** in `capevolve.yaml` — one runner
(`run-optimizer`) resolves the name via `skills/optimizers/registry.yaml`:

```yaml
capabilities:    [system-prompt, tools]   # any of: system-prompt | tools | mcp-tool | skill-package
optimizer_skill: claude-code              # ← swap: codex | gemini-cli | opencode | openclaw | ibm-bob | generic | mock
algorithm_skill: hill-climb               # hill-climb (--focus all|cyclic|hardest-first) | gepa | skillopt
num_trials: 4
store: git                                # versions every iteration
```

**Extending is just as small:** a new capability, algorithm, or optimizer is one
folder or one `optimizers/registry.yaml` row — see
[`docs/EXTENDING.md`](docs/EXTENDING.md).

## How it works

**intake → implement-and-check → baseline → optimize → finalize → report.**

Intake collects inputs and scaffolds the project. Implement-and-check is a hard
gate: `cap-evolve check` refuses to proceed until the adapter is real and
deterministic. Baseline freezes a seeded train/val/test split (test **sealed**) and
scores the seed on val. Each optimize iteration **diagnoses** failing val traces →
the optimizer **proposes** one edit → the candidate is **evaluated** on val (each
trial gets its own seed, so pass^k measures real variance) → a **paired
significance gate** (Δ > k·SE) accepts or rejects → the iteration is committed and
memory updated. Finalize scores the best candidate on the **sealed test split
exactly once**; report writes `report.md` and a self-contained `dashboard.html`.

> **Honesty is enforced in code, not docs.** Splitting, reward aggregation, the
> gate, and sealing test all live in `cap_evolve`
> ([`docs/HONEST_EVAL.md`](docs/HONEST_EVAL.md)). Infra-vs-capability failures are
> distinguished by a structured `Rollout.error` signal, never by string-matching
> feedback prose.

## How it compares

| | cap-evolve | DSPy | GEPA | promptfoo |
|---|:--:|:--:|:--:|:--:|
| Optimizes prompts | ✅ | ✅ | ✅ | ❌ (eval only) |
| Optimizes tools/MCP + skill packages | ✅ | ➖ | ➖ | ❌ |
| Sealed test + significance gate enforced in code | ✅ | ➖ | ➖ | ➖ |
| Host- & agent-agnostic (no framework lock-in) | ✅ | ❌ | ❌ | ➖ |
| Onboard a benchmark from a single prompt | ✅ | ❌ | ❌ | ➖ |
| Git-versioned iterations + optimizer memory | ✅ | ❌ | ❌ | ❌ |
| Live cost-aware dashboard | ✅ | ❌ | ❌ | ➖ |
| Zero runtime dependencies | ✅ | ❌ | ❌ | ❌ |

Roadmap: [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Skill library

cap-evolve is a library of **18** [Agent Skills](https://www.anthropic.com/news/skills)
over a tiny stdlib core. The 8 per-CLI optimizers collapsed into one
`run-optimizer` skill + a one-row-per-optimizer registry; the three hill-climb
variants collapsed into one `hill-climb` skill with `--focus`.

| Component | Skills |
|-----------|--------|
| orchestrate  | `orchestrate` · `using-cap-evolve` (session-start router) |
| phases       | `intake` · `implement-and-check` · `baseline` · `evaluate` · `diagnose` · `gate` · `finalize` · `report` |
| capabilities | `system-prompt` · `skill-package` · `tools` · `mcp-tool` |
| algorithms   | `hill-climb` (`--focus all\|cyclic\|hardest-first`) · `gepa` · `skillopt` |
| optimizers   | `run-optimizer` + `optimizers/registry.yaml` (`claude-code`, `codex`, `gemini-cli`, `opencode`, `openclaw`, `ibm-bob`, `generic`, `mock`) |

`gepa` (real GEPA — reflective Pareto search, two-stage minibatch-then-full-val
economy; arXiv:2507.19457) and `skillopt` (epochs × mini-batches with a decaying
textual learning rate; arXiv:2605.23904) are the sample-efficient **flagships**;
`hill-climb` is the simple global-best baseline climber.

**Claude Code plugin:** `claude --plugin-dir ./plugins/cap-evolve` exposes every
skill as `/cap-evolve:<skill>` and arms honesty **hooks** (PreToolUse denies edits
to the sealed test/gold; Stop/SubagentStop block finishing until `cap-evolve check`
and the gate are green) — all in **core-owned scripts**, never in editable skill
markdown.

## Results

<!-- RESULTS: filled from examples/tau2_airline/run_full -->

> Real [tau2-bench](https://github.com/sierra-research/tau2-bench) airline run —
> optimizing the airline policy + tools with a `claude-opus-4-6` optimizer and
> `gpt-oss-120b` (agent + user simulator, via IBM RITS) over all 50 tasks. Numbers
> come from the latest run in
> [`examples/tau2_airline/run_full/`](examples/tau2_airline/run_full/) (`report.md` /
> `dashboard.html`); every iteration is a git commit. Reproduce from zero:
> [`docs/REPRODUCE_tau2.md`](docs/REPRODUCE_tau2.md).

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

**Acknowledgements.** The `gepa` and `skillopt` skills are independent
implementations of the GEPA (arXiv:2507.19457) and SkillOpt (arXiv:2605.23904)
papers — no third-party code is included; both reference projects are MIT-licensed.
cap-evolve also draws on ideas from DSPy, tau-bench/tau2-bench, and the Agent Skills
standard. Full citations: [docs/sources.bib](docs/sources.bib).

## License

MIT.
