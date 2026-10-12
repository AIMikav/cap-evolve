"""v2 D6, SpreadsheetBench half: the candidate's skills/ reaches the executor read-only, alone, outside the
agent view, and a crash in the candidate's own helper is tagged error_kind=candidate. No docker, no model:
the sandbox server, the vendored executor and litellm are faked."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ADAPTER_DIR = REPO / "templates" / "adapters" / "spreadsheetbench"
JUPYTER = REPO / "third_party" / "spreadsheetbench" / "code_exec_docker" / "jupyter.py"


@pytest.fixture()
def mod(monkeypatch):
    monkeypatch.delenv("CAPEVOLVE_HELPERS", raising=False)
    for p in (REPO / "core", ADAPTER_DIR, ADAPTER_DIR.parent):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    spec = importlib.util.spec_from_file_location("_sb_adapter_helpers", ADAPTER_DIR / "adapter.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "_skill_sandboxes", {})
    return m


def _cand(root: Path, name: str, scripts: dict, prompt="solve {skills_root}") -> Path:
    c = root / name
    (c / "skills/fmt/scripts").mkdir(parents=True)
    (c / "skills/fmt/SKILL.md").write_text("---\nname: fmt\ndescription: formats cells\n---\n", encoding="utf-8")
    for k, v in scripts.items():
        (c / "skills/fmt/scripts" / k).write_text(v, encoding="utf-8")
    (c / "prompt.md").write_text(prompt, encoding="utf-8")
    return c


# ---- the vendored executor: a second, read-only bind ----------------------------------------------
def test_vendored_jupyter_adds_a_second_read_only_bind_only_when_configured():
    src = JUPYTER.read_text(encoding="utf-8")
    assert "'bind': '/mnt/skills', 'mode': 'ro'" in src
    assert "docker_config.get('skills_path')" in src       # absent key => the original single volume
    assert src.count("'mode': 'rw'") == 1 and "volumes=volumes" in src


# ---- one server per distinct skills tree: only the current candidate is visible -----------------------
class _FakeSandbox:
    def __init__(self, skills_src=None):
        self.skills_src, self.leases, self.down = skills_src, 0, False

    def shutdown(self):
        self.down = True


def _patch_env(monkeypatch, mod, tmp_path):
    monkeypatch.setattr(mod, "_Sandbox", _FakeSandbox)
    monkeypatch.setattr(mod, "_preflight_mount", lambda d: None)
    monkeypatch.setattr(mod, "_data_dir", lambda: tmp_path / "view")
    monkeypatch.setenv("CAPEVOLVE_HELPERS", "1")


def test_each_distinct_skills_tree_gets_its_own_sandbox_and_same_tree_shares(mod, monkeypatch, tmp_path):
    _patch_env(monkeypatch, mod, tmp_path)
    a, b, a2 = (_cand(tmp_path, n, {"f.py": body}) for n, body in (("a", "x=1\n"), ("b", "x=2\n"), ("a2", "x=1\n")))
    sa, sb, sa2 = (mod._get_skills_sandbox(c / "skills") for c in (a, b, a2))
    assert sa is not sb and sa is sa2 and sa.leases == 2 and sb.leases == 1
    mod._release_skills_sandbox(sa)
    assert sa.leases == 1


def test_idle_skills_sandboxes_are_evicted_busy_ones_are_not(mod, monkeypatch, tmp_path):
    _patch_env(monkeypatch, mod, tmp_path)
    monkeypatch.setattr(mod, "MAX_SKILL_SANDBOXES", 2)
    boxes = []
    for i in range(2):
        boxes.append(mod._get_skills_sandbox(_cand(tmp_path, f"c{i}", {"f.py": f"x={i}\n"}) / "skills"))
    mod._release_skills_sandbox(boxes[0])           # c0 idle, c1 still leased
    boxes.append(mod._get_skills_sandbox(_cand(tmp_path, "c2", {"f.py": "x=9\n"}) / "skills"))
    assert boxes[0].down and not boxes[1].down and not boxes[2].down
    assert len(mod._skill_sandboxes) == 2


# ---- the real _Sandbox constructor, with the server process faked ----------------------------------------
def test_sandbox_config_binds_only_the_staged_current_skills_outside_the_view(mod, monkeypatch, tmp_path):
    view = tmp_path / "view"
    (view / "spreadsheet").mkdir(parents=True)
    code = tmp_path / "vendor"
    code.mkdir()
    for n in ("api.py", "jupyter.py"):
        (code / n).write_text("# fake\n")
    monkeypatch.setattr(mod._Vendor, "get", classmethod(lambda cls: {"code_dir": code}))
    monkeypatch.setattr(mod, "_data_dir", lambda: view)
    monkeypatch.setattr(mod._Sandbox, "_wait_ready", lambda self, timeout=30.0: None)
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **k: types.SimpleNamespace(poll=lambda: 0))
    other = _cand(tmp_path, "other", {"secret_other.py": "x=0\n"})
    cur = _cand(tmp_path, "cur", {"norm.py": "x=1\n"})
    sb = mod._Sandbox(cur / "skills")
    cfg = json.loads((sb.runtime_dir / "config.json").read_text())
    staged = Path(cfg["skills_path"])
    assert staged != view and view.resolve() not in staged.resolve().parents
    assert (staged / "fmt/scripts/norm.py").is_file()
    assert not list(staged.rglob("secret_other.py")) and other.exists()
    assert not os.access(staged / "fmt/scripts/norm.py", os.W_OK) or os.geteuid() == 0
    # without skills the config is the v1 one
    plain = mod._Sandbox()
    assert "skills_path" not in json.loads((plain.runtime_dir / "config.json").read_text())
    for dp, _, _ in os.walk(staged):
        os.chmod(dp, 0o755)


def test_the_skills_gold_free_check_extends_the_mount_check(mod, tmp_path):
    view = tmp_path / "view"
    view.mkdir()
    ok = tmp_path / "ok"
    (ok / "a").mkdir(parents=True)
    (ok / "a/f.py").write_text("x=1\n")
    mod._check_skills_mount_is_gold_free(ok, view)
    (ok / "a/1_x_answer.py").write_text("y=1\n")
    with pytest.raises(RuntimeError, match="gold-named"):
        mod._check_skills_mount_is_gold_free(ok, view)
    with pytest.raises(RuntimeError, match="overlaps the agent view"):
        mod._check_skills_mount_is_gold_free(view / "skills", view)  # nested inside the view


# ---- {skills_root} -----------------------------------------------------------------------------------------
def test_skills_root_token_is_resolved_only_when_helpers_are_on(mod, monkeypatch, tmp_path):
    c = _cand(tmp_path, "c", {})
    (c / "task_template.md").write_text(
        "{instruction} {spreadsheet_path} {spreadsheet_content} {instruction_type} {answer_position} "
        "{output_path} see {skills_root}\n", encoding="utf-8")
    assert "unknown placeholder" in mod._task_template_error(c)       # v1 behaviour: not a known field
    monkeypatch.setenv("CAPEVOLVE_HELPERS", "1")
    assert mod._task_template_error(c) is None
    assert "see /mnt/skills" in mod._read_task_template(c)


# ---- run_target: staging refusals and the fault tag ---------------------------------------------------------
class _Box:
    def client_for(self, conv):
        return object()

    def release(self, conv):
        pass


def _run(mod, monkeypatch, tmp_path, cand, exec_outputs):
    view = tmp_path / "view"
    (view / "spreadsheet/t1").mkdir(parents=True)
    (view / "spreadsheet/t1/1_t1_input.xlsx").write_bytes(b"PK")
    entry = {"id": "t1", "instruction": "i", "answer_position": "A1", "instruction_type": "Cell-Level Manipulation"}
    monkeypatch.setattr(mod, "_data_dir", lambda: view)
    monkeypatch.setattr(mod, "_entries_by_id", lambda: {"t1": entry})
    monkeypatch.setattr(mod._Vendor, "get", classmethod(lambda cls: {
        "extract_code": None, "exec_code": lambda client, code: exec_outputs.pop(0)}))
    monkeypatch.setattr(mod, "_extract_python", lambda vendor, text: "run()")
    monkeypatch.setattr(mod, "_workbook_context", lambda *a, **k: "preview")
    monkeypatch.setattr(mod, "_resolve_case_file", lambda d, i, sid, kind: d / "1_t1_input.xlsx")
    monkeypatch.setattr(mod, "_get_skills_sandbox", lambda src: _Box())
    monkeypatch.setattr(mod, "_release_skills_sandbox", lambda sb: None)
    monkeypatch.setattr(mod, "MAX_TURNS", 1)
    resp = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="```python\nrun()\n```"))],
        usage=None, _hidden_params={})
    fake = types.ModuleType("litellm")
    fake.completion = lambda **k: resp
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr(mod.model_config, "MODEL", "m", raising=False)
    monkeypatch.setattr(mod.model_config, "llm_kwargs", lambda: {}, raising=False)
    return mod.Adapter().run_target(types.SimpleNamespace(id="t1"), cand)


TB = ('Traceback (most recent call last):\n  File "<stdin>", line 2, in <module>\n'
      '  File "/mnt/skills/fmt/scripts/norm.py", line 4, in norm\nValueError: bad cell\n')


def test_a_helper_crash_with_no_output_is_a_candidate_fault(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("CAPEVOLVE_HELPERS", "1")
    r = _run(mod, monkeypatch, tmp_path, _cand(tmp_path, "c", {"norm.py": "x=1\n"}), [TB])
    assert r.error and "candidate helper crashed: ValueError: bad cell" in r.error
    assert r.metadata["error_kind"] == "candidate" and "ValueError" in r.metadata["helper_fault"]


def test_an_infra_style_traceback_outside_the_helper_is_not_tagged(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("CAPEVOLVE_HELPERS", "1")
    own = 'Traceback (most recent call last):\n  File "<stdin>", line 2, in <module>\nKeyError: 1\n'
    r = _run(mod, monkeypatch, tmp_path, _cand(tmp_path, "c", {"norm.py": "x=1\n"}), [own])
    assert (r.metadata or {}).get("error_kind") != "candidate"


def test_helpers_off_never_tags_or_mounts(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_get_sandbox", lambda: _Box())
    r = _run(mod, monkeypatch, tmp_path, _cand(tmp_path, "c", {"norm.py": "x=1\n"}), [TB])
    assert (r.metadata or {}).get("error_kind") != "candidate"


def test_an_unmountable_skills_tree_is_refused_before_any_container(mod, monkeypatch, tmp_path):
    monkeypatch.setenv("CAPEVOLVE_HELPERS", "1")
    c = _cand(tmp_path, "c", {"norm.py": "x=1\n"})
    os.symlink("/etc/hostname", c / "skills/fmt/scripts/l.py")
    r = _run(mod, monkeypatch, tmp_path, c, [])
    assert "skills/ tree refused" in r.error and r.metadata["error_kind"] == "candidate"
