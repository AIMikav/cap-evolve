# gemini-cli optimizer

Google Gemini CLI headless as the edit proposer.

    gemini -p "<instructions>" --approval-mode=yolo [-m <model>]

run with `cwd=<workdir>`. `-p` forces non-interactive; `--approval-mode=yolo`
auto-approves actions (`--yolo` is deprecated in favor of it).

- **Install:** https://github.com/google-gemini/gemini-cli
- **Auth:** `gemini` auth flow, or `GEMINI_API_KEY`.
- **JSON:** `--output-format json`.
