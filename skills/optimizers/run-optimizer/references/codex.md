# codex optimizer

OpenAI Codex CLI headless as the edit proposer.

    codex exec --sandbox workspace-write [-m <model>] "<instructions>"

run with `cwd=<workdir>`. `codex exec` is the non-interactive subcommand;
`--sandbox workspace-write` permits file writes with no network (`--full-auto` is
deprecated in favor of it).

- **Install:** https://developers.openai.com/codex
- **Auth:** `codex login`, or `OPENAI_API_KEY`.
- **JSON:** `--json`.
