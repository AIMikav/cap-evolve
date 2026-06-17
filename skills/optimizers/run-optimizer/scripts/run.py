"""run-optimizer — the single runner for every shell-invokable edit proposer.

The optimize loop calls this with ``--name <optimizer> --workdir <candidate copy>
--prompt <INSTRUCTIONS.md>``. This script reads ``optimizers/registry.yaml``,
resolves the named row, builds the command from its ``command_template`` (with
``{workdir}`` / ``{prompt}`` / ``{prompt_text}`` / ``{model}`` placeholders), and
runs it with cwd = the workdir so the agent edits files in place. One runner +
one YAML row per optimizer replaces the eight near-identical wrapper skills.

Backward-compat: a spec that still names an old optimizer skill (``claude-code``,
``ibm-bob``, ``mock``, …) resolves to the registry row of the same name, so old
specs keep working unchanged.

The ``mock`` row is fully offline: it runs the colocated ``_mock_apply.py`` (a
deterministic JSON-driven editor), so zero-API e2e tests never touch a network.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from cap_evolve.specfile import read_yaml

# Old per-CLI skill names that callers may still pass; they map 1:1 to a row.
_LEGACY_ALIASES = {
    "claude-code", "codex", "gemini-cli", "ibm-bob", "openclaw",
    "opencode", "generic", "mock",
}


def _registry_path() -> Path:
    """``optimizers/registry.yaml`` — sibling of the ``run-optimizer`` skill dir."""
    env = os.environ.get("CAPEVOLVE_OPTIMIZER_REGISTRY")
    if env and Path(env).exists():
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "registry.yaml"
        if cand.exists() and parent.name == "optimizers":
            return cand
        cand = parent / "optimizers" / "registry.yaml"
        if cand.exists():
            return cand
    raise FileNotFoundError("optimizers/registry.yaml not found (set CAPEVOLVE_OPTIMIZER_REGISTRY)")


def load_registry() -> dict:
    return read_yaml(_registry_path().read_text(encoding="utf-8"))


def _self_dir() -> str:
    return str(Path(__file__).resolve().parent)


def _resolve_env_keys(row: dict) -> dict:
    """Resolve auth env vars; for ibm-bob also read the nearest .env (legacy behavior).

    Returns ``{present: [keys set], env: {overrides}}``. Never prints values.
    """
    keys = [k.strip() for k in str(row.get("env_keys", "")).split(",") if k.strip()]
    present, overrides = [], {}
    for k in keys:
        if os.environ.get(k):
            present.append(k)
    # ibm-bob: BOBSHELL_API_KEY <- BOB_API_KEY or the nearest .env up the tree.
    if "BOBSHELL_API_KEY" in keys and not os.environ.get("BOBSHELL_API_KEY"):
        val = os.environ.get("BOB_API_KEY") or _read_dotenv_key(("BOBSHELL_API_KEY", "BOB_API_KEY"))
        if val:
            overrides["BOBSHELL_API_KEY"] = val
            present.append("BOBSHELL_API_KEY(.env)")
    return {"present": present, "env": overrides}


def _read_dotenv_key(names: tuple[str, ...]) -> str | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        env = parent / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    name, val = line.split("=", 1)
                    if name.strip() in names:
                        return val.strip().strip('"').strip("'")
            break
    return None


def build_command(template: str, *, workdir: str, prompt: str, prompt_text: str,
                  model: str | None, self_dir: str) -> list[str]:
    """Expand the template into argv.

    ``{model}`` drops itself and an immediately-preceding bare flag token
    (``-m`` / ``--model``) when no model is set, so the same template works with
    or without a model. ``${VAR}`` expands from the environment first (so the
    ``generic`` / ``openclaw`` escape-hatch rows pull their command from env).
    """
    expanded = os.path.expandvars(template)
    tokens = shlex.split(expanded)
    out: list[str] = []
    subs = {"workdir": workdir, "prompt": prompt, "prompt_text": prompt_text,
            "self_dir": self_dir, "model": model or ""}
    for tok in tokens:
        # model-group drop: a flag token whose only job is to precede {model}
        if tok in ("-m", "--model", "-model") and not model:
            # peek: drop only if the next token IS the {model} placeholder
            continue
        if tok == "{model}" and not model:
            continue
        new = tok
        for k, v in subs.items():
            new = new.replace("{" + k + "}", v)
        out.append(new)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run-optimizer")
    p.add_argument("--name", default=os.environ.get("CAPEVOLVE_OPTIMIZER", "mock"),
                   help="optimizer name resolved via optimizers/registry.yaml")
    p.add_argument("--workdir", help="candidate working copy to edit in place")
    p.add_argument("--prompt", help="path to INSTRUCTIONS.md")
    p.add_argument("--model", default=os.environ.get("CAPEVOLVE_OPTIMIZER_MODEL") or None)
    p.add_argument("--list", action="store_true", help="list known optimizers and exit")
    args = p.parse_args(argv)

    registry = load_registry()
    if args.list:
        print(json.dumps({"optimizers": sorted(registry)}, indent=2))
        return 0
    if not args.workdir or not args.prompt:
        p.error("--workdir and --prompt are required (unless --list)")

    name = args.name
    row = registry.get(name)
    if row is None:
        # back-compat: an unknown legacy alias still maps by exact name; otherwise error.
        print(json.dumps({"optimizer": name, "error":
              f"no registry row for {name!r}; known: {sorted(registry)}"}))
        return 2

    # Resolve to an absolute path: the command runs with cwd=workdir, so a relative
    # {workdir} would be re-interpreted against that cwd (nesting the path). Making
    # it absolute keeps file edits landing in the candidate dir, not a subdir of it.
    workdir = str(Path(args.workdir).resolve())
    prompt_path = Path(args.prompt).resolve()
    prompt_text = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

    cmd = build_command(str(row.get("command_template", "")), workdir=workdir,
                        prompt=str(prompt_path), prompt_text=prompt_text,
                        model=args.model, self_dir=_self_dir())
    if not cmd:
        print(json.dumps({"optimizer": name, "error":
              "empty command — for generic/openclaw set the *_CMD env var"}))
        return 2

    # CLI present? (mock's helper is a python script we ship, always present.)
    exe = cmd[0]
    if str(row.get("offline", "")).lower() != "true" and shutil.which(exe) is None:
        print(json.dumps({"optimizer": name, "cli_present": False, "error":
              f"`{exe}` not on PATH. Install: {row.get('install_url') or 'see references'}. "
              f"Auth: {row.get('auth_notes')}"}))
        return 2

    auth = _resolve_env_keys(row)
    env = dict(os.environ)
    env.update(auth["env"])

    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, env=env)
    print(json.dumps({"optimizer": name, "cli_present": True, "returncode": proc.returncode,
                      "auth_present": auth["present"],
                      "stdout_tail": proc.stdout[-800:], "stderr_tail": proc.stderr[-500:]}))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
