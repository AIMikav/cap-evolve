from pathlib import Path

from conftest import BASE_EVENTS


def test_discover_finds_run_dirs(tmp_base, make_run):
    from capevolve_dashboard import runs
    make_run("run_a", events=BASE_EVENTS)
    make_run("run_b", events=BASE_EVENTS)
    (tmp_base / "not_a_run").mkdir()
    found = runs.discover(tmp_base)
    names = sorted(p.name for p in found)
    assert names == ["run_a", "run_b"]


def test_list_runs_projects_light_summary(tmp_base, make_run):
    from capevolve_dashboard import runs
    make_run("run_a", events=BASE_EVENTS,
             baseline={"val": {"reward": 0.25}, "best_id": "seed"})
    rows = runs.list_runs(tmp_base)
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "run_a"
    assert row["baseline_val"] == 0.25
    assert row["best_val"] == 0.75
    assert row["iterations"] == 2
    assert row["status"] in {"live", "done", "failed"}


def test_load_run_returns_graph_and_summary(tmp_base, make_run):
    from capevolve_dashboard import runs
    make_run("run_a", events=BASE_EVENTS,
             baseline={"val": {"reward": 0.25}, "best_id": "seed"})
    data = runs.load_run(tmp_base, "run_a")
    assert data["run_id"] == "run_a"
    assert "graph" in data and "summary" in data
    assert data["graph"]["best_id"] == "cand_0001"


def test_load_run_missing_raises(tmp_base):
    import pytest
    from capevolve_dashboard import runs
    with pytest.raises(runs.RunNotFound):
        runs.load_run(tmp_base, "run_nope")
