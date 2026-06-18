"""FastAPI app factory serving cap-evolve run data (read-only)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from . import runs, trajectories


def create_app(base_dir: Path) -> FastAPI:
    base = Path(base_dir)
    app = FastAPI(title="cap-evolve dashboard", version="0.1.0")
    app.state.base_dir = base

    @app.get("/api/health")
    def health():
        return {"ok": True, "base_dir": str(base)}

    @app.get("/api/runs")
    def get_runs():
        return runs.list_runs(base)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        try:
            return runs.load_run(base, run_id)
        except runs.RunNotFound:
            raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    @app.get("/api/runs/{run_id}/rollouts")
    def get_rollouts(run_id: str, split: str | None = Query(default=None)):
        path = base / run_id
        if not (path / "events.jsonl").exists():
            raise HTTPException(status_code=404, detail="run not found")
        return trajectories.list_rollouts(path, split)

    @app.get("/api/runs/{run_id}/rollout/{file_name}")
    def get_rollout(run_id: str, file_name: str):
        path = base / run_id
        try:
            return trajectories.read_rollout(path, file_name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="rollout not found")

    @app.get("/api/runs/{run_id}/diff/{candidate}")
    def get_diff(run_id: str, candidate: str):
        path = base / run_id
        if not (path / "events.jsonl").exists():
            raise HTTPException(status_code=404, detail="run not found")
        return trajectories.diff_candidate(path, candidate)

    @app.get("/api/compare")
    def get_compare(ids: str = Query(...)):
        from . import compare
        run_ids = [x for x in ids.split(",") if x]
        return compare.compare_runs(base, run_ids)

    return app
