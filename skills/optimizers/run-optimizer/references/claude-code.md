# claude-code optimizer

Claude Code headless as the edit proposer.

    claude -p "<instructions>" --permission-mode acceptEdits [--model <id>]

run with `cwd=<workdir>`. `-p/--print` runs non-interactively and exits;
`--permission-mode acceptEdits` lets it write files without prompting.

- **Install:** https://docs.claude.com/claude-code
- **Auth:** a logged-in Claude Code session, or `ANTHROPIC_API_KEY`.
- **JSON:** `--output-format json` (used by the headless cost capture).
