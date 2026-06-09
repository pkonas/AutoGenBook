from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .paths import default_run_paths


@dataclass(frozen=True)
class RunContext:
    run_id: str
    out_dir: Path
    mode: str
    structure_graph_path: Path
    agent_state_path: Path

    @classmethod
    def create(cls, out_dir: Path, mode: str) -> "RunContext":
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        paths = default_run_paths(out_dir)
        return cls(
            run_id=ts,
            out_dir=out_dir,
            mode=mode,
            structure_graph_path=paths["structure_graph"],
            agent_state_path=paths["agent_state"],
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "out_dir": str(self.out_dir),
            "mode": self.mode,
            "structure_graph_path": str(self.structure_graph_path),
            "agent_state_path": str(self.agent_state_path),
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "RunContext":
        return cls(
            run_id=str(data.get("run_id", "")),
            out_dir=Path(data.get("out_dir", ".")),
            mode=str(data.get("mode", "book")),
            structure_graph_path=Path(data.get("structure_graph_path", "")),
            agent_state_path=Path(data.get("agent_state_path", "")),
        )
