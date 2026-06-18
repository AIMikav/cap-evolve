# Design: tau2-bench airline + RITS, from scratch, production-ready & recorded

**Date:** 2026-06-18
**Author:** Osher Elhadad
**Status:** Draft for review

## Goal

Make cap-evolve's flagship example a **from-scratch, zero-assumption, recorded**
optimization of **tau2-bench airline** using **IBM RITS** (`openai/gpt-oss-120b` as
both agent and user simulator) and a **Claude Code (`claude-opus-4-6`) optimizer**.
The whole thing must run **autonomously from one prompt**, be **easy to reproduce**,
and produce artifacts for a **demo video**. Then refactor the repo's examples and
docs to production quality.

This replaces the existing bundled `examples/tau2_airline` (which relies on a
runtime monkeypatch and a vendored task file) with a clean integration built
against a freshly cloned upstream tau2-bench.

## Non-goals

- Changing cap-evolve's core honesty machinery (splits/gate/seal). We use it as-is.
- Maintaining a fork of tau2-bench.
- Beating a specific score. The number is whatever the honest run produces; for a
  no-holdout split it is reported as a **fit metric**, not a held-out result.

## The run, exactly

| Knob | Value |
|---|---|
| Benchmark | tau2-bench **airline**, cloned fresh from `github.com/sierra-research/tau2-bench` (**latest main**) into `../tau2-bench` |
| Tasks / splits | all **50** airline tasks as **train = val = test** (no-holdout fit metric; engine logs `splits_warning`) |
| Runner (agent + user sim) | `openai/gpt-oss-120b` via **IBM RITS** |
| RITS integration | **litellm config shim** (`rits.py`): resolve per-model RITS endpoint, set litellm globals + `RITS_API_KEY` header, use model string `hosted_vllm/openai/gpt-oss-120b` |
| Concurrency | `TAU2_MAX_CONCURRENCY=100` |
| num_trials | **10** (per-trial seed → real pass^k) |
| Optimizer | **claude-code** @ **`claude-opus-4-6`** |
| Algorithm | **hill-climb** `--focus all` |
| Capabilities | `[system-prompt, tools]` (airline policy + tool docstrings/code) |
| max_iterations | **10** |
| Budget | `max_usd: 400`, `max_optimizer_usd: 400`; per-iteration ≈ $40 approximated via `optimizer_max_turns` (cap-evolve has **no** per-iteration $ cap — documented limitation) |
| Gate | auto (paired significance), `gate_k_se` per tuning |

**Cost/time reality (acknowledged):** val 50 × 10 trials × 10 iters ≈ 5,000 val
rollouts + baseline (~500) + sealed test (~500) ≈ **~6,000 full airline
conversations** through gpt-oss as both agent and user. Many hours of RITS load at
concurrency 100. RITS runner cost is **not** dollar-tracked; the $400 budget governs
the **optimizer (Claude)**.

## Architecture

### Component 1 — `setup.sh` (from-scratch, reproducible)
Single command. Responsibilities:
1. Clone `sierra-research/tau2-bench` latest main into `../tau2-bench` (skip if present), `pip install -e ../tau2-bench`.
2. `pip install ./core` (cap-evolve-core).
3. **Record the resolved tau2-bench commit SHA** into `run_full/TAU2_COMMIT.txt` + echo it (so "latest main" is still reproducible after the fact).
4. Verify: `python -c "import tau2"`, RITS reachable (`RITS_API_KEY` present + info endpoint 200), `claude --version`, `cap-evolve version`.
5. Print a green "ready" line or a precise remediation message. No silent failure.

Pin the Python interpreter explicitly (the repo has both miniforge `cap-evolve` and a
`.venv`); `setup.sh` resolves and prints the interpreter that has `cap_evolve` importable.

### Component 2 — `rits.py` (RITS via litellm config shim — Approach A)
- `load_env()`: parse repo-root `.env` (walk parents), `setdefault` so it never clobbers real env.
- `resolve_endpoint(model_name)`: query RITS info endpoint (retry w/ backoff), map model → endpoint path, build `api_base = {API_URL}/{endpoint}/v1`.
- `configure_litellm()`: set `litellm.api_base`/`HOSTED_VLLM_API_BASE`, `HOSTED_VLLM_API_KEY`, and `litellm.headers = {"RITS_API_KEY": key}` once at import. Model string handed to tau2 = `hosted_vllm/openai/gpt-oss-120b`.
- Keep the proven robustness: gpt-oss empty-turn retry (env-gated), `TAU2_LLM_TIMEOUT`, `TAU2_LLM_RETRIES`, `TAU2_BATCH_TIMEOUT` watchdog, `TAU2_INFRA_RETRIES`.
- **No monkeypatch of `litellm.acompletion`.** If tau2's runner ignores litellm globals for some call path, fall back is documented but Approach A is the target.

### Component 3 — `adapter.py` (cap-evolve `CapabilityAdapter`)
- `tasks("all")`: pull the **50 airline tasks directly from tau2-bench** (`tau2.runner.get_tasks` / domain task loader) — no duplicated `airline.jsonl`. Deterministic order. Honors `CAPEVOLVE_TAU2_TASK_IDS` subset for smoke tests.
- `run_batch(tasks, ctx, *, seed)`: drive tau2's concurrent runner once per trial with `seed = base + k`; agent+user = RITS model; `num_trials=1` inside tau2 (harness owns trials). Wrap stdout→stderr to protect JSON. Mark `INFRASTRUCTURE`-terminated runs as `Rollout.error` (noise, not capability).
- `run_target` delegates to `run_batch([task])`.
- `score(task, rollout)`: tau2 reward ∈ [0,1] + gold-aware feedback (missed required actions / info). Deterministic.
- `materialize(cand_dir, edits)`: pure write of policy/tools into cand_dir.
- `live(cand_dir)`: context manager that injects the candidate policy + tools into tau2's airline domain for one eval (snapshot-restore pristine each time).
- Must pass `cap-evolve check` (no stubs, stable tasks, deterministic scorer, pure materialize).

### Component 4 — seed capability
`seed_caps/policy/policy.md` + `seed_caps/tools/tools.py`, seeded from tau2-bench's
own airline domain (canonical, "from scratch"). Tool stubs whose docstrings are the
live tool descriptions the agent reads.

### Component 5 — run config + launcher
- `capevolve.yaml` with the exact knobs in the table above.
- `run.sh`: sets env (`TAU2_MAX_CONCURRENCY=100`, timeouts, infra retries, PYTHONPATH, CAPEVOLVE_*), then `cap-evolve run --spec ... --project ...`. One command.
- `smoke.sh`: same but tiny (`CAPEVOLVE_TAU2_TASK_IDS` of 2 ids, `num_trials 1`, `max_iterations 1`, optimizer `mock` or 1-turn claude) to prove end-to-end before spending.

### Component 6 — recording
- `brew install asciinema agg` (in setup or DEMO.md).
- Record `setup.sh` → `smoke.sh` → `run.sh` launch → `open dashboard.html` into a `.cast` under `run_full/`.
- `DEMO.md`: storyboard + exact commands + how to render `.cast` → GIF/MP4 (`agg`) → narration beats. Includes the resolved tau2 SHA.
- Ship the run's `dashboard.html` + `report.md` + `events.jsonl` under `run_full/`.

### Component 7 — cleanup + docs to production
- **Delete** `examples/date_tool`, `examples/json_extract`, `examples/skills_bench`, old `examples/tau2_airline`.
- **Keep** `examples/toy_calc`; verify it still runs and matches the README quickstart (it's the zero-API CI gate).
- Rewrite: README Quickstart + tau2 worked example + Results (re-measured), `docs/REPRODUCE_tau2.md`, examples table, skill/example counts, and **purge every reference** to the deleted examples across README/docs/CI.
- Update CI if it referenced deleted examples (keep toy_calc as the gate).

## Process / sequencing (the build plan)

1. `setup.sh` + clone tau2-bench + verify imports/RITS/claude.
2. `rits.py` + `adapter.py` + `seed_caps` + `capevolve.yaml`.
3. `cap-evolve check` green (hard gate).
4. `smoke.sh`: 2 tasks / 1 trial / 1 iter, prove the full pipeline runs autonomously end to end. Fix every bug. **General (non-tau-specific) bugs are fixed in `core/`, not patched around in the example**, and noted in the spec/changelog.
5. Re-run smoke until clean.
6. Launch full `run.sh` (background) and record. Monitor for terminal failure signatures.
7. On completion: capture dashboard/report, re-measure Results.
8. Cleanup examples + rewrite docs.
9. Final `cap-evolve check` + toy_calc CI gate green; verify docs commands run verbatim.

## Risks & mitigations
- **tau2 latest-main API drift** → adapter is defensive about tau2's import surface; record SHA; if a breaking change appears, fix in adapter and note it.
- **litellm globals not honored by some tau2 call path** → Approach A first; if a path bypasses globals, document and add the narrowest possible hook (still not a fork).
- **Long wall-clock / RITS load** → smoke first; full run in background with a Monitor watching for `Traceback|INFRASTRUCTURE|FAILED|Killed|max_usd` plus progress.
- **$40/iter not hard-enforceable** → set total caps + `optimizer_max_turns`; document the limitation in README and DEMO.md. (Optional follow-up: add a per-iteration optimizer $ cap to core — out of scope unless desired.)
- **Multiple debug runs consume RITS + minor optimizer $** → accepted per user.

## Deliverables
`examples/tau2_airline/{setup.sh, rits.py, adapter.py, capevolve.yaml, run.sh, smoke.sh, seed_caps/, README.md, DEMO.md, run_full/{dashboard.html, report.md, events.jsonl, TAU2_COMMIT.txt, *.cast}}`; refactored top-level README + `docs/REPRODUCE_tau2.md`; deleted examples; verified toy_calc + CI.

## Open items confirmed with user
RITS = litellm config shim (A). tau2 = latest main (+ record SHA). Optimizer = claude-opus-4-6 + turns cap. Recording = asciinema + DEMO.md + dashboard. Execution = smoke-then-full. Examples = remove all but keep/verify toy_calc.
