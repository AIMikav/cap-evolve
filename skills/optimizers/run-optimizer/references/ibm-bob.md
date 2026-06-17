# ibm-bob optimizer

IBM Bob Shell (the `bob` CLI) as the edit proposer, run non-interactively in the
candidate workdir:

    bob --accept-license --yolo --chat-mode code --hide-intermediary-output [-m <model>] "<instructions>"

- `--yolo` (a.k.a. `--approval-mode yolo`) auto-approves all actions so Bob can
  write files (the workdir is a throwaway candidate copy).
- `--accept-license` accepts the IBM license on first run (needed in fresh/CI envs).
- The positional prompt is the non-interactive one-shot form (`-p/--prompt` is
  deprecated upstream).
- **Auth:** Bob reads `BOBSHELL_API_KEY`. The runner populates it from
  `BOBSHELL_API_KEY` → `BOB_API_KEY` (env or the nearest repo `.env`).
- **Install:** `curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash -s -- --package-manager npm`
