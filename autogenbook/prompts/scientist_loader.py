from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

DEFAULT_SCIENTIST_PROMPT_FILES: Dict[str, str] = {
    "global_system_policy": "global_system_policy.md",
    "global_evidence_instructions": "global_evidence_instructions.md",
    "json_only_system": "json_only_system.md",
    "json_repair_system": "json_repair_system.md",
    "mcp_tools_system": "mcp_tools_system.md",
    "idea_agent_system": "idea_agent_system.md",
    "idea_agent_user": "idea_agent_user.md",
    "literature_agent_system": "literature_agent_system.md",
    "literature_agent_user": "literature_agent_user.md",
    "plan_agent_system": "plan_agent_system.md",
    "plan_agent_user": "plan_agent_user.md",
    "code_patch_agent_system": "code_patch_agent_system.md",
    "code_patch_agent_user": "code_patch_agent_user.md",
    "analyze_agent_system": "analyze_agent_system.md",
    "analyze_agent_user": "analyze_agent_user.md",
    "review_agent_system": "review_agent_system.md",
    "review_agent_user": "review_agent_user.md",
    "revision_agent_system": "revision_agent_system.md",
    "revision_agent_user": "revision_agent_user.md",
    "paper_section_writer_system": "paper_section_writer_system.md",
    "paper_section_writer_user": "paper_section_writer_user.md",
    "length_control_system": "length_control_system.md",
    "length_control_user": "length_control_user.md",
}


def _default_prompt_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts" / "scientist"


def load_scientist_prompts(prompt_dir: Optional[Path] = None) -> Dict[str, str]:
    base = Path(prompt_dir) if prompt_dir is not None else _default_prompt_dir()
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Scientist prompt directory not found: {base}")

    prompts: Dict[str, str] = {}
    for key, filename in DEFAULT_SCIENTIST_PROMPT_FILES.items():
        path = base / filename
        if not path.exists():
            raise FileNotFoundError(f"Scientist prompt file not found: {path}")
        prompts[key] = path.read_text(encoding="utf-8")
    return prompts
