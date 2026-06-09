from __future__ import annotations

from pathlib import Path
from typing import Dict


def default_run_paths(out_dir: Path) -> Dict[str, Path]:
    return {
        "structure_graph": out_dir / "structure_graph.json",
        "agent_state": out_dir / "agent_state.json",
        "logs_dir": out_dir / "logs",
        "run_log": out_dir / "logs" / "run.log",
    }
