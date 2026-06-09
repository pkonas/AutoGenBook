from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

DEFAULT_PROPOSAL_PROMPT_FILES: Dict[str, str] = {
    "llm1_architect_system": "llm1_architect_system.md",
    "llm2_researcher_outline_system": "llm2_researcher_outline_system.md",
    "llm3_opponent_outline_system": "llm3_opponent_outline_system.md",
    "llm4_researcher_writer_system": "llm4_researcher_writer_system.md",
    "llm5_opponent_final_system": "llm5_opponent_final_system.md",
    "metadata_extractor_system": "metadata_extractor_system.md",
    "json_only_system": "json_only_system.md",
    "json_repair_system": "json_repair_system.md",
    "mcp_tools_system": "mcp_tools_system.md",
}


def _default_prompt_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts" / "proposal"


def load_proposal_prompts(prompt_dir: Optional[Path] = None) -> Dict[str, str]:
    base = Path(prompt_dir) if prompt_dir is not None else _default_prompt_dir()
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Proposal prompt directory not found: {base}")

    prompts: Dict[str, str] = {}
    for key, filename in DEFAULT_PROPOSAL_PROMPT_FILES.items():
        path = base / filename
        if not path.exists():
            raise FileNotFoundError(f"Proposal prompt file not found: {path}")
        prompts[key] = path.read_text(encoding="utf-8")
    return prompts
