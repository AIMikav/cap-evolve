"""Contract: report summarizes baseline → best val → sealed test and writes
report.md from the run dir's baseline.json / final.json.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import _bootstrap  # noqa: F401

from cap_evolve.skillcheck import Checker, import_run, quiet, temp_run_dir


def main() -> int:
    c = Checker("report")
    run = import_run()
    c.require_main(run)

    with tempfile.TemporaryDirectory() as d:
        rd, _ = temp_run_dir(Path(d))
        (rd.root / "baseline.json").write_text(
            json.dumps({"val": {"reward": 0.4}, "best_id": "seed"}), encoding="utf-8")
        (rd.root / "final.json").write_text(
            json.dumps({"test": {"reward": 0.8, "pass_k": 0.7}, "best_id": "cand_0001"}),
            encoding="utf-8")

        with quiet():
            rc = run.main(["--run-dir", str(rd.root), "--no-dashboard"])
        c.check(rc == 0, "report returned nonzero")

        md_path = rd.root / "report.md"
        c.check(md_path.exists(), "report.md was not written", note="writes report.md")
        md = md_path.read_text()
        c.check("0.4" in md and "0.8" in md,
                f"report.md missing baseline/test numbers:\n{md}",
                note="report carries baseline → sealed test")
        c.check("sealed" in md.lower(),
                "report does not state the test was scored once on the sealed split")

    return c.emit()


if __name__ == "__main__":
    sys.exit(main())
