from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def write_agent_io(
    out_dir: Path,
    agent_name: str,
    input_dict: Dict[str, Any],
    output_dict: Any,
    meta: Optional[Dict[str, Any]] = None,
) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = out_dir / "agent_logs" / agent_name
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent": agent_name,
        "timestamp": ts,
        "input": input_dict,
        "output": output_dict,
        "meta": meta or {},
    }
    path = log_dir / f"{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
