"""Deterministic helper code in the editable surface (v2, D6).

A procedure the target regenerates on every rollout is stochastic; the same procedure as a script in
the candidate's own surface is the same bytes every trial. This module is the framework half: the
config, the surface manifest, the static lint, staging of a skill tree for a read-only mount, the
fault tag for a helper's own crash, and the determinism metric. The runtime half (how the files
reach the target) belongs to each adapter; the pre-evaluation smoke check is ``helper_smoke``.

Everything is OFF unless ``helpers.enabled`` (spec) or ``CAPEVOLVE_HELPERS=1`` (env, wins) is set,
so v1 runs are unchanged. Spec::

    surface:
      executable_dirs: ["skills/*/scripts"]   # globs (relative to the candidate root) that MAY hold
                                              # executable files; default when helpers are on:
                                              # skills/*/scripts and tools
    helpers:
      enabled: true
      lint: true                # block task ids / answer literals / task-identity branches / network
      smoke: true               # helper_smoke stage in the pregate (0 rollouts charged on failure)
      suffixes: [".py"]         # what counts as a helper file under executable_dirs
      max_file_bytes: 65536
      max_total_bytes: 262144
      smoke_timeout: 20         # seconds per smoke subprocess
      task_ids: [...]           # extra ids the lint refuses as literals (or ids_file: path, one per line)
      forbidden_literals: [...] # answer values copied from traces/gold, refused as literals
      protected_patterns: [...] # regexes for evaluator/gold paths a helper must never name or open
      deny_roots: [...]         # absolute dirs the smoke subprocess may not read
      fixtures: path.jsonl      # recorded-trace fixtures for the smoke (rows: see ``helper_smoke``)
"""

from __future__ import annotations

import ast
import json
import os
import re
import statistics
from fnmatch import fnmatch
from pathlib import Path

DEFAULTS: dict = {
    "enabled": False, "lint": True, "smoke": True,
    "suffixes": [".py"], "max_file_bytes": 65536, "max_total_bytes": 262144, "smoke_timeout": 20.0,
    "task_ids": [], "ids_file": None, "forbidden_literals": [], "protected_patterns": None,
    "deny_roots": [], "fixtures": None,
}
#: Where helpers may live when ``helpers.enabled`` and the surface declares nothing.
DEFAULT_EXECUTABLE_DIRS = ["skills/*/scripts", "tools"]
#: Files that are executable no matter where they sit: outside the declared dirs they are refused.
EXEC_SUFFIXES = {".py", ".sh", ".bash", ".js", ".so", ".pyc", ".pyd", ".exe"}
#: What a staged skill tree may hold (a skill is text + scripts, never binaries or workbooks).
SKILL_SUFFIXES = {".py", ".md", ".json", ".txt", ".yaml", ".yml"}
#: Evaluator / gold / split / dataset paths a helper must not name or open.
DEFAULT_PROTECTED = [r"(?i)golden", r"(?i)ground[_-]?truth", r"(?i)(^|[/\\._-])answer(s)?([._/\\-]|$)",
                     r"(?i)evaluat(or|ion)\.py", r"(?i)dataset\.json", r"(?i)(^|[/\\])splits?([/\\._]|$)",
                     r"(?i)(^|[/\\])gold([/\\._-]|$)"]
_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


# ---- config -----------------------------------------------------------------------------------
def _env_enabled():
    raw = os.environ.get("CAPEVOLVE_HELPERS", "").strip().lower()
    if not raw:
        return None
    if raw in _TRUE | _FALSE:
        return raw in _TRUE
    raise ValueError(f"CAPEVOLVE_HELPERS={raw!r}: expected one of {sorted(_TRUE | _FALSE)}")


def resolve(spec: dict | None = None) -> dict:
    """Effective helper config for ``spec`` (defaults overlaid by ``helpers:``; env wins for
    ``enabled``). ``executable_dirs`` comes from ``surface.executable_dirs``. An unknown key raises:
    a typo must not silently run the wrong experiment."""
    spec = spec if isinstance(spec, dict) else {}
    tbl = spec.get("helpers")
    tbl = tbl if isinstance(tbl, dict) else {}
    bad = sorted(set(tbl) - set(DEFAULTS))
    if bad:
        raise ValueError(f"unknown helpers key(s) {bad}; valid keys: {sorted(DEFAULTS)}")
    cfg = {**DEFAULTS, **tbl}
    env = _env_enabled()
    if env is not None:
        cfg["enabled"] = env
    cfg["enabled"] = bool(cfg["enabled"])
    surf = spec.get("surface")
    dirs = surf.get("executable_dirs") if isinstance(surf, dict) else None
    if dirs is None:
        dirs = DEFAULT_EXECUTABLE_DIRS if cfg["enabled"] else []
    if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
        raise ValueError("surface.executable_dirs must be a list of glob strings")
    cfg["executable_dirs"] = list(dirs)
    return cfg


def enabled(spec: dict | None = None) -> bool:
    return resolve(spec)["enabled"]


# ---- surface manifest -------------------------------------------------------------------------
def _real_files(root: Path):
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if "__pycache__" not in p.parts and (p.is_file() or p.is_symlink()):
                yield p


def _dir_globs(cand: Path, dirs) -> list[Path]:
    out: list[Path] = []
    for pat in dirs:
        out += [p for p in sorted(cand.glob(pat)) if p.is_dir()]
    return out


def executable_files(cand: Path, cfg: dict) -> list[Path]:
    """Helper files in the candidate: regular files with a helper suffix under a declared dir."""
    cand, seen = Path(cand), {}
    for d in _dir_globs(cand, cfg["executable_dirs"]):
        for f in _real_files(d):
            if f.suffix in cfg["suffixes"] and not f.is_symlink():
                seen[f] = None
    return sorted(seen)


def surface_problems(cand: Path, cfg: dict) -> list[str]:
    """Violations of the surface manifest: executable files outside the declared dirs, symlinks
    among them, and size caps. Empty when helpers are off or nothing is declared."""
    cand = Path(cand)
    if not cfg["enabled"] or not cfg["executable_dirs"]:
        return []
    problems: list[str] = []
    declared = {p.resolve() for d in _dir_globs(cand, cfg["executable_dirs"]) for p in [d]}

    def under_declared(p: Path) -> bool:
        rp = p.resolve()
        return any(rp == d or d in rp.parents for d in declared)

    for f in _real_files(cand):
        rel = f.relative_to(cand).as_posix()
        if f.is_symlink():
            if under_declared(f) or f.suffix in EXEC_SUFFIXES:
                problems.append(f"{rel}: symlink in an executable surface")
            continue
        if f.suffix in EXEC_SUFFIXES and not under_declared(f):
            problems.append(f"{rel}: executable file outside the declared surface "
                            f"{cfg['executable_dirs']}")
    total = 0
    for f in executable_files(cand, cfg):
        size = f.stat().st_size
        total += size
        if size > cfg["max_file_bytes"]:
            problems.append(f"{f.relative_to(cand).as_posix()}: {size} bytes > max_file_bytes "
                            f"{cfg['max_file_bytes']}")
    if total > cfg["max_total_bytes"]:
        problems.append(f"helper files total {total} bytes > max_total_bytes {cfg['max_total_bytes']}")
    return problems


def changed_files(cand: Path, parent: Path | None, files: list[Path]) -> list[Path]:
    """The subset of ``files`` (under ``cand``) that is new or differs from the parent's bytes."""
    if parent is None or not Path(parent).is_dir():
        return list(files)
    out = []
    for f in files:
        pf = Path(parent) / f.relative_to(cand)
        if not pf.is_file() or pf.read_bytes() != f.read_bytes():
            out.append(f)
    return out


# ---- lint -------------------------------------------------------------------------------------
_NET_MODULES = {"socket", "ssl", "urllib", "urllib3", "http", "httpx", "requests", "aiohttp", "ftplib",
                "smtplib", "telnetlib", "xmlrpc", "websocket", "websockets", "paramiko", "poplib",
                "imaplib", "socketserver"}
_PROC_MODULES = {"subprocess", "multiprocessing", "pty", "ctypes", "cffi"}
_PROC_ATTRS = {"system", "popen", "execv", "execve", "execl", "execlp", "execvp", "spawnl", "spawnv",
               "spawnlp", "spawnvp", "posix_spawn", "fork", "forkpty", "startfile"}
_ID_NAME = re.compile(r"(?i)(^|_)(task|sample|instance|problem|example|case|row|sheet)_?(id|name|idx|index)$"
                      r"|^(tid|sid|task)$")
_TABLE_MIN = 20  # constants in one literal collection before it is "a lookup table"


def _id_matchers(cfg: dict) -> tuple[set[str], list[str]]:
    ids = {str(i) for i in cfg.get("task_ids") or []}
    if cfg.get("ids_file"):
        ids |= {ln.strip() for ln in Path(cfg["ids_file"]).read_text(encoding="utf-8").splitlines()
                if ln.strip()}
    return ids, [str(x) for x in cfg.get("forbidden_literals") or [] if str(x).strip()]


def _name_of(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        sl = node.slice
        return sl.value if isinstance(sl, ast.Constant) and isinstance(sl.value, str) else _name_of(node.value)
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    return ""


def lint_source(src: str, rel: str, cfg: dict) -> list[str]:
    """Findings for one helper file (empty = clean). Deterministic and conservative: a necessary
    filter, not proof of generality (held-out behaviour is what the promotion rule measures)."""
    try:
        tree = ast.parse(src, filename=rel)
    except SyntaxError as e:
        return [f"{rel}:{e.lineno}: does not compile ({e.msg})"]
    ids, lits = _id_matchers(cfg)
    prot = [re.compile(p) for p in (cfg.get("protected_patterns") or DEFAULT_PROTECTED)]
    out: list[str] = []

    def add(node, msg):
        out.append(f"{rel}:{getattr(node, 'lineno', 0)}: {msg}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                top = m.split(".")[0]
                if top in _NET_MODULES:
                    add(node, f"network module '{m}' (helpers run offline)")
                elif top in _PROC_MODULES:
                    add(node, f"process/foreign-code module '{m}' (no subprocess in a helper)")
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                for a in node.names:
                    if a.name in _PROC_ATTRS:
                        add(node, f"os.{a.name} (no subprocess in a helper)")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in _PROC_ATTRS and _name_of(f.value) == "os":
                add(node, f"os.{f.attr}() (no subprocess in a helper)")
            if isinstance(f, ast.Name) and f.id in {"eval", "exec", "__import__", "compile"} and node.args:
                if f.id == "__import__" and isinstance(node.args[0], ast.Constant) \
                        and str(node.args[0].value).split(".")[0] not in _NET_MODULES | _PROC_MODULES:
                    continue
                add(node, f"{f.id}() hides what the helper does from this lint")
        elif isinstance(node, ast.Compare):
            names = [_name_of(n) for n in [node.left, *node.comparators]]
            consts = [n for n in [node.left, *node.comparators] if isinstance(n, ast.Constant)]
            if consts and any(_ID_NAME.search(n) for n in names if n):
                add(node, "branches on task identity (compares a task/sample id to a constant)")
            for c in [n for n in [node.left, *node.comparators] if isinstance(n, (ast.Tuple, ast.List, ast.Set))]:
                if any(isinstance(e, ast.Constant) for e in c.elts) and any(_ID_NAME.search(n) for n in names if n):
                    add(node, "branches on task identity (membership in a constant id set)")
        elif isinstance(node, ast.Match):
            if _ID_NAME.search(_name_of(node.subject) or ""):
                add(node, "branches on task identity (match on a task/sample id)")
        elif isinstance(node, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
            elts = list(node.values if isinstance(node, ast.Dict) else node.elts)
            consts = [e for e in elts if isinstance(e, ast.Constant) or
                      (isinstance(e, (ast.Tuple, ast.List)) and e.elts and all(isinstance(x, ast.Constant) for x in e.elts))]
            if len(consts) >= _TABLE_MIN and len(consts) == len(elts) and \
                    sum(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in consts) \
                    + sum(isinstance(e, (ast.Tuple, ast.List)) for e in consts) >= _TABLE_MIN:
                add(node, f"literal lookup table of {len(consts)} constants (answers must be computed, "
                          "not stored)")
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float)) \
                and not isinstance(node.value, bool):
            v = node.value
            sv = str(v)
            if isinstance(v, str):
                for pat in prot:
                    if pat.search(v):
                        add(node, f"names an evaluator/gold/split path ({v[:60]!r})")
                        break
            if sv in ids or any(len(i) >= 4 and re.search(rf"(?<![\w.]){re.escape(i)}(?![\w.])", sv)
                                for i in ids if isinstance(v, str)):
                add(node, f"task id literal {sv[:40]!r}")
            elif any(sv == L or (isinstance(v, str) and len(L) >= 4 and L in sv) for L in lits):
                add(node, f"literal copied from a trace/answer ({sv[:40]!r})")
    # a task id as a bare identifier or keyword is the same defect
    seen = set()
    return [o for o in out if not (o in seen or seen.add(o))]


def _strip_line(finding: str) -> str:
    return re.sub(r"^([^:]+):\d+:", r"\1:", finding)


def lint_candidate(cand: Path, cfg: dict, parent: Path | None = None) -> dict:
    """Lint every NEW or CHANGED helper file (an old file the parent already carried is not this
    edit's fault). ``{ok, findings, files}``."""
    cand = Path(cand)
    files = changed_files(cand, parent, executable_files(cand, cfg))
    findings: list[str] = []
    for f in files:
        rel = f.relative_to(cand).as_posix()
        got = lint_source(f.read_text(encoding="utf-8", errors="replace"), rel, cfg)
        pf = Path(parent) / rel if parent is not None else None
        if got and pf is not None and pf.is_file():
            # a changed file keeps the findings the parent already had (a seed tools.py is not this edit's fault)
            old = {_strip_line(x) for x in lint_source(pf.read_text(encoding="utf-8", errors="replace"), rel, cfg)}
            got = [x for x in got if _strip_line(x) not in old]
        findings += got
    return {"ok": not findings, "findings": findings,
            "files": [f.relative_to(cand).as_posix() for f in files]}


# ---- carrying a skills tree into the target's kernel -----------------------------------------------
#: Frames of a helper carry this prefix as their filename, so a crash inside one is recognisable.
KERNEL_ROOT = "capevolve_skills"
BOOTSTRAP_OK = "CAPEVOLVE_HELPERS_READY"
_GOLD_NAME = re.compile(r"(_answer|_golden|^golden|^answer)", re.I)
SKILL_SUFFIXES = {".py", ".md", ".json", ".txt", ".yaml", ".yml"}


def skills_tree_problems(root: Path) -> list[str]:
    """Gold-free / sanity check for a candidate's skills tree: regular text files only, no symlinks, no
    gold-named file, no file type outside ``SKILL_SUFFIXES``."""
    root, bad = Path(root), []
    if root.is_symlink():
        return [f"{root}: skills root is a symlink"]
    for p in sorted(root.rglob("*")) if root.is_dir() else []:
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in p.parts or p.name == ".gitkeep":
            continue
        if p.is_symlink():
            bad.append(f"{rel}: symlink")
        elif p.is_file():
            if _GOLD_NAME.search(p.name):
                bad.append(f"{rel}: gold-named file")
            elif p.suffix.lower() not in SKILL_SUFFIXES:
                bad.append(f"{rel}: file type {p.suffix or '(none)'} is not allowed in a skills tree")
    return bad


def _ident(name: str) -> str:
    return re.sub(r"\W", "_", name)


def kernel_bootstrap(skills_dir: Path, max_bytes: int = 262144) -> str:
    """Python source that, run ONCE in the target's kernel, defines every ``skills/<skill>/scripts/*.py`` as
    ``skills.<skill>.<script>`` (``-`` becomes ``_``) and prints ``BOOTSTRAP_OK``. The target only ever receives
    TEXT of the current candidate's own files: nothing is mounted, nothing sits under the data view, and the
    kernel (one per rollout) keeps the modules through the case-2/3 replay. Frames carry the filename
    ``capevolve_skills/<skill>/<script>.py`` (see ``helper_crash_in``). Raises ValueError on an oversize tree."""
    skills_dir = Path(skills_dir)
    mods, total = [], 0
    for f in sorted(skills_dir.glob("*/scripts/*.py")):
        src = f.read_text(encoding="utf-8")
        total += len(src.encode())
        mods.append((_ident(f.parent.parent.name), _ident(f.stem), f"{KERNEL_ROOT}/{f.parent.parent.name}/{f.name}", src))
    if total > max_bytes:
        raise ValueError(f"helper scripts total {total} bytes > {max_bytes}")
    lines = ["import sys as _s, types as _t",
             "_p = _s.modules.get('skills')",
             "if _p is None:\n    _p = _s.modules['skills'] = _t.ModuleType('skills'); _p.__path__ = []"]
    lines.append("def _load(skill, stem, fname, src):\n"
                 "    pk = _s.modules.get('skills.' + skill)\n"
                 "    if pk is None:\n"
                 "        pk = _s.modules['skills.' + skill] = _t.ModuleType('skills.' + skill); pk.__path__ = []\n"
                 "        setattr(_p, skill, pk)\n"
                 "    m = _s.modules['skills.%s.%s' % (skill, stem)] = _t.ModuleType('skills.%s.%s' % (skill, stem))\n"
                 "    m.__file__ = fname; setattr(pk, stem, m)\n"
                 "    exec(compile(src, fname, 'exec'), m.__dict__)")
    lines += [f"_load({sk!r}, {st!r}, {fn!r}, {src!r})" for sk, st, fn, src in mods]
    lines.append(f"print({BOOTSTRAP_OK!r}, {len(mods)})")
    return "\n".join(lines) + "\n"


def bootstrap_ok(exec_result: str) -> bool:
    return BOOTSTRAP_OK in (exec_result or "")


def skills_prompt(src: Path, max_chars: int = 6000) -> str:
    """Each ``skills/<name>/SKILL.md`` (frontmatter dropped, name + description kept, body capped) for the
    system message, so the agent knows which ``skills.<name>.<script>`` functions exist."""
    out = []
    for md in sorted(Path(src).glob("*/SKILL.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"---\s*\n(.*?)\n---\s*\n?", text, re.S)
        fm, body = (m.group(1), text[m.end():]) if m else ("", text)
        name = (re.search(r"(?m)^name:\s*(.+)$", fm) or [None, md.parent.name])[1].strip()
        desc = (re.search(r"(?m)^description:\s*(.+)$", fm) or [None, ""])[1].strip()
        out.append(f"## Helper skill `{name}` (import as skills.{_ident(md.parent.name)}.<script>)\n"
                   f"{desc}\n\n{body.strip()[:max_chars]}")
    return "\n\n".join(out)


SKILLS_ROOT_TOKEN = "{skills_root}"


def render_skills_root(text: str, root: str) -> str:
    """Plain ``str.replace`` (not ``.format``): literal braces in prompt text stay safe. For the kernel
    carrier ``root`` is the import root, ``skills`` (``from {skills_root}.<skill> import <script>``)."""
    return text.replace(SKILLS_ROOT_TOKEN, root)


def has_skills(cand: Path) -> bool:
    d = Path(cand) / "skills"
    return d.is_dir() and any(p.is_file() for p in d.rglob("*"))


# ---- a helper's own crash is the candidate's fault ----------------------------------------------
#: A helper crash carries this prefix, so a harness that only sees tool-error TEXT (tau2 puts the
#: exception message in the tool message) can still attribute it, even with sims running in parallel.
HELPER_FAULT_MARKER = "CANDIDATE_HELPER_FAULT"


class CandidateHelperError(RuntimeError):
    """Raised by ``candidate_helper`` when a helper the candidate added or changed fails."""


def candidate_helper(fn):
    """Decorator for helper methods in a candidate's ``tools.py``: any exception is re-raised as
    ``CandidateHelperError('CANDIDATE_HELPER_FAULT: <name>: <type>: <msg>')``. The tau adapters call
    ``fault_in_messages`` on the sim to set ``error_kind=candidate`` (reward 0, not missing)."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except CandidateHelperError:
            raise
        except Exception as e:  # noqa: BLE001 — the point is to type EVERY failure of the helper
            raise CandidateHelperError(
                f"{HELPER_FAULT_MARKER}: {fn.__name__}: {type(e).__name__}: {e}") from e
    return wrapper


def fault_in_text(text) -> str | None:
    """The first helper-fault line in ``text`` (a tool message / exception string), else None."""
    if isinstance(text, str) and HELPER_FAULT_MARKER in text:
        i = text.index(HELPER_FAULT_MARKER)
        return text[i:].splitlines()[0][:300]
    return None


def fault_in_messages(messages) -> str | None:
    """First helper-fault line anywhere in a recorded conversation (list of dicts or strings)."""
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else m
        if isinstance(content, list):
            content = " ".join(str(c.get("text", c)) if isinstance(c, dict) else str(c) for c in content)
        hit = fault_in_text(content if isinstance(content, str) else json.dumps(content, default=str))
        if hit:
            return hit
    return None


def candidate_fault_metadata(detail: str, **extra) -> dict:
    """Rollout metadata for a helper's own crash (``errored_trial_policy: fail`` scores it 0)."""
    return {"error_kind": "candidate", "helper_fault": str(detail)[:300], **extra}


_TB_FILE = re.compile(r'File "([^"]+)", line (\d+)')


def helper_crash_in(exec_results, skills_root: str) -> str | None:
    """A traceback in executor output whose frames include a file under ``skills_root`` (``KERNEL_ROOT``: the
    target ran the candidate's helper and it raised). Returns the last line of that traceback, else None."""
    root = skills_root.rstrip("/") + "/"
    for text in reversed(list(exec_results or [])):
        if "Traceback" not in text:
            continue
        if any(m.group(1).startswith(root) for m in _TB_FILE.finditer(text)):
            tail = [ln for ln in text.strip().splitlines() if ln.strip()]
            return tail[-1][:300] if tail else "helper raised"
    return None


# ---- determinism metric --------------------------------------------------------------------------
def reward_variance(rewards: dict) -> dict:
    """Per-task reward variance across trials (sample variance; tasks with < 2 trials are skipped).
    ``rewards``: {task_id: [reward per trial]}. Returns {per_task, mean_variance, flaky_fraction
    (tasks whose trials disagree), n_tasks}. 0 = fully deterministic."""
    per = {str(t): statistics.variance([float(x) for x in v])
           for t, v in rewards.items() if v is not None and len([x for x in v if x is not None]) >= 2
           for v in [[x for x in v if x is not None]]}
    n = len(per)
    return {"per_task": per, "n_tasks": n,
            "mean_variance": (sum(per.values()) / n) if n else None,
            "flaky_fraction": (sum(1 for x in per.values() if x > 0) / n) if n else None}


def variance_against(candidate: dict, champion: dict) -> dict:
    """``reward_variance`` outputs of a candidate and the champion/seed, plus the per-task and mean
    deltas on the tasks both measured (negative = the candidate is more deterministic)."""
    shared = sorted(set(candidate["per_task"]) & set(champion["per_task"]))
    d = {t: candidate["per_task"][t] - champion["per_task"][t] for t in shared}
    return {"per_task_delta": d, "n_shared": len(shared),
            "mean_delta": (sum(d.values()) / len(d)) if d else None}
