# openclaw optimizer

OpenClaw as the edit proposer. OpenClaw's headless CLI flags are less
standardized than claude/codex/gemini, so this row reads the command from the
environment:

```bash
export CAPEVOLVE_OPENCLAW_CMD='openclaw run --workspace {workdir} "{prompt_text}"'
```

- **Install / auth:** per your OpenClaw build. Verify the headless flags against
  your installed version.
