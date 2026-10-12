"""v2 D6: the helper config, the surface manifest, the lint, skills staging, the fault tag, the metric."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cap_evolve import helpers  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("CAPEVOLVE_HELPERS", raising=False)


def _cand(tmp_path, files: dict) -> Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


# ---- config: v1 defaults ---------------------------------------------------------------------
def test_default_is_off_and_declares_nothing():
    cfg = helpers.resolve({})
    assert cfg["enabled"] is False and cfg["executable_dirs"] == []
    assert helpers.surface_problems(Path("/nonexistent"), cfg) == []


def test_enabled_by_spec_env_wins_and_typos_raise(monkeypatch):
    assert helpers.enabled({"helpers": {"enabled": True}})
    assert helpers.resolve({"helpers": {"enabled": True}})["executable_dirs"] == helpers.DEFAULT_EXECUTABLE_DIRS
    monkeypatch.setenv("CAPEVOLVE_HELPERS", "0")
    assert not helpers.enabled({"helpers": {"enabled": True}})
    monkeypatch.setenv("CAPEVOLVE_HELPERS", "1")
    assert helpers.enabled({})
    with pytest.raises(ValueError):
        helpers.resolve({"helpers": {"enabeld": True}})


def test_surface_manifest_dirs_come_from_the_spec():
    cfg = helpers.resolve({"helpers": {"enabled": True}, "surface": {"executable_dirs": ["skills/*/scripts"]}})
    assert cfg["executable_dirs"] == ["skills/*/scripts"]


# ---- surface manifest ------------------------------------------------------------------------
def test_executable_file_outside_declared_dirs_is_refused(tmp_path):
    cfg = helpers.resolve({"helpers": {"enabled": True}, "surface": {"executable_dirs": ["skills/*/scripts"]}})
    c = _cand(tmp_path, {"skills/a/scripts/f.py": "x=1\n", "skills/a/SKILL.md": "# a\n",
                         "prompt.md": "hi", "extra/evil.py": "x=2\n", "skills/a/run.sh": "echo\n"})
    probs = helpers.surface_problems(c, cfg)
    assert any("extra/evil.py" in p for p in probs) and any("skills/a/run.sh" in p for p in probs)
    assert not any("skills/a/scripts/f.py" in p for p in probs)
    assert [f.name for f in helpers.executable_files(c, cfg)] == ["f.py"]


def test_size_caps_and_symlinks(tmp_path):
    cfg = {**helpers.resolve({"helpers": {"enabled": True, "max_file_bytes": 10}}),
           "executable_dirs": ["skills/*/scripts"]}
    c = _cand(tmp_path, {"skills/a/scripts/big.py": "x = 1\n" * 10})
    os.symlink("/etc/passwd", c / "skills/a/scripts/link.py")
    probs = helpers.surface_problems(c, cfg)
    assert any("max_file_bytes" in p for p in probs) and any("symlink" in p for p in probs)


# ---- lint ------------------------------------------------------------------------------------
def _lint(src, **cfgkw):
    cfg = helpers.resolve({"helpers": {"enabled": True, **cfgkw}})
    return helpers.lint_source(src, "skills/a/scripts/f.py", cfg)


def test_a_parameterised_helper_passes():
    assert _lint("import re, json\n\ndef norm(cells, width=3):\n    return [re.sub(r'\\s+', ' ', c).strip() for c in cells][:width]\n") == []


@pytest.mark.parametrize("src,needle", [
    ("def f():\n    return '50768'\n", "task id literal"),
    ("def f(sid):\n    if sid == 'x1':\n        return 1\n", "task identity"),
    ("def f(task_id):\n    if task_id in ('a', 'b'):\n        return 1\n", "task identity"),
    ("def f(task):\n    return task['task_id'] != 'zz'\n", "task identity"),
    ("import socket\n", "network module"),
    ("import requests\n", "network module"),
    ("from urllib.request import urlopen\n", "network module"),
    ("import subprocess\n", "process"),
    ("import os\n\ndef f():\n    os.system('ls')\n", "os.system"),
    ("def f():\n    return open('/data/gold/1_answer.xlsx')\n", "evaluator/gold"),
    ("P = '/srv/splits/test.json'\n", "evaluator/gold"),
    ("def f():\n    return eval('1')\n", "eval()"),
])
def test_lint_blocks(src, needle):
    out = _lint(src, task_ids=["50768"])
    assert out and any(needle in x for x in out), out


def test_lint_blocks_copied_answer_literals_and_lookup_tables():
    assert any("copied from a trace" in x for x in _lint("X = 'Total: 1,234.50 USD'\n", forbidden_literals=["Total: 1,234.50 USD"]))
    table = "T = {" + ", ".join(f"'k{i}': 'v{i}'" for i in range(25)) + "}\n"
    assert any("lookup table" in x for x in _lint(table))
    assert _lint("T = {'a': 'b', 'c': 'd'}\n") == []


def test_lint_only_blames_new_findings_on_a_changed_file(tmp_path):
    cfg = helpers.resolve({"helpers": {"enabled": True}})
    parent = _cand(tmp_path / "p", {"tools/tools.py": "import socket\n\ndef a():\n    return 1\n"})
    cand = _cand(tmp_path / "c", {"tools/tools.py": "import socket\n\ndef a():\n    return 2\n"})
    assert helpers.lint_candidate(cand, cfg, parent)["ok"]
    cand2 = _cand(tmp_path / "c2", {"tools/tools.py": "import socket\nimport subprocess\n"})
    r = helpers.lint_candidate(cand2, cfg, parent)
    assert not r["ok"] and all("subprocess" in f for f in r["findings"])


def test_lint_ids_file(tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("abc-123\n", encoding="utf-8")
    assert _lint("X = 'abc-123'\n", ids_file=str(ids))


# ---- staging: read-only, gold-free ------------------------------------------------------------
def test_staging_is_read_only_and_copies_only_the_skills_tree(tmp_path):
    src = tmp_path / "cand" / "skills"
    _cand(src, {"a/SKILL.md": "---\nname: a\ndescription: d\n---\n", "a/scripts/f.py": "x=1\n"})
    dest = tmp_path / "stage" / "skills"
    assert helpers.stage_skills(src, dest) == 2
    f = dest / "a/scripts/f.py"
    assert f.read_text() == "x=1\n"
    assert not (f.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    assert not ((dest / "a").stat().st_mode & stat.S_IWUSR)
    assert helpers.skills_tree_problems(dest) == []
    for dp, _, _ in os.walk(dest):          # let pytest clean up
        os.chmod(dp, 0o755)


def test_staging_refuses_symlinks_gold_names_binaries_and_the_agent_view(tmp_path):
    src = tmp_path / "skills"
    _cand(src, {"a/scripts/f.py": "x=1\n"})
    os.symlink("/etc/hostname", src / "a/scripts/l.py")
    with pytest.raises(helpers.HelperStagingError, match="symlink"):
        helpers.stage_skills(src, tmp_path / "d1")
    (src / "a/scripts/l.py").unlink()
    (src / "a/scripts/1_x_answer.py").write_text("y=1\n")
    with pytest.raises(helpers.HelperStagingError, match="gold-named"):
        helpers.stage_skills(src, tmp_path / "d2")
    (src / "a/scripts/1_x_answer.py").unlink()
    (src / "a/blob.xlsx").write_bytes(b"PK")
    with pytest.raises(helpers.HelperStagingError, match="not allowed"):
        helpers.stage_skills(src, tmp_path / "d3")
    (src / "a/blob.xlsx").unlink()
    view = tmp_path / "view"
    (view / "spreadsheet").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="outside the agent view"):
        helpers.stage_skills(src, view / "spreadsheet" / "skills", forbid_inside=view)


def test_hash_tracks_content_and_token_is_a_plain_replace():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        s = Path(d)
        _cand(s, {"a/scripts/f.py": "x=1\n"})
        h1 = helpers.skills_hash(s)
        (s / "a/scripts/f.py").write_text("x=2\n")
        assert helpers.skills_hash(s) != h1
    assert helpers.render_skills_root("see {skills_root}/a and {literal}", "/mnt/skills") == "see /mnt/skills/a and {literal}"


def test_skills_index_reads_frontmatter(tmp_path):
    _cand(tmp_path, {"a/SKILL.md": "---\nname: alpha\ndescription: does a thing\n---\nbody\n"})
    assert helpers.skills_index(tmp_path) == "- alpha: does a thing"


# ---- a helper's own crash is the candidate's fault ---------------------------------------------
def test_candidate_helper_types_every_failure():
    @helpers.candidate_helper
    def boom(x):
        raise KeyError(x)

    with pytest.raises(helpers.CandidateHelperError) as e:
        boom("k")
    assert helpers.fault_in_text(str(e.value)).startswith(helpers.HELPER_FAULT_MARKER)
    msgs = [{"role": "assistant", "content": "ok"}, {"role": "tool", "content": f"Error: {e.value}"}]
    assert "boom" in helpers.fault_in_messages(msgs)
    assert helpers.fault_in_messages([{"role": "tool", "content": "Error: no such reservation"}]) is None
    assert helpers.candidate_fault_metadata("x")["error_kind"] == "candidate"


def test_helper_crash_in_executor_output_needs_a_frame_under_the_skills_root():
    tb = ('Traceback (most recent call last):\n  File "<stdin>", line 3, in <module>\n'
          '  File "/mnt/skills/a/scripts/f.py", line 9, in norm\nZeroDivisionError: division by zero\n')
    assert helpers.helper_crash_in(["fine", tb], "/mnt/skills") == "ZeroDivisionError: division by zero"
    own = 'Traceback (most recent call last):\n  File "<stdin>", line 3, in <module>\nKeyError: 1\n'
    assert helpers.helper_crash_in([own], "/mnt/skills") is None


# ---- determinism metric ----------------------------------------------------------------------
def test_reward_variance():
    r = helpers.reward_variance({"t1": [1, 1, 1], "t2": [0, 1, 1], "t3": [1], "t4": [0, None, 0]})
    assert r["n_tasks"] == 3 and r["per_task"]["t1"] == 0 and r["per_task"]["t4"] == 0
    assert r["per_task"]["t2"] == pytest.approx(1 / 3)
    assert r["flaky_fraction"] == pytest.approx(1 / 3)
    seed = helpers.reward_variance({"t1": [0, 1, 1], "t2": [0, 1, 1]})
    d = helpers.variance_against(r, seed)
    assert d["n_shared"] == 2 and d["per_task_delta"]["t1"] < 0 and d["per_task_delta"]["t2"] == 0
    assert helpers.reward_variance({})["mean_variance"] is None
