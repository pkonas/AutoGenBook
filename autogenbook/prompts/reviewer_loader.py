from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

DEFAULT_REVIEWER_PROMPT_FILES: Dict[str, str] = {
    "prompt1_default": "prompt1_default.md",
    "prompt2": "prompt2.md",
    "architect_prompt": "architect_prompt.md",
    "reviewer_system": "reviewer_system.md",
    "reviewer_user": "reviewer_user.md",
}


def _default_prompt_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "prompts" / "reviewer"


def load_reviewer_prompts(prompt_dir: Optional[Path] = None) -> Dict[str, str]:
    base = Path(prompt_dir) if prompt_dir is not None else _default_prompt_dir()
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Reviewer prompt directory not found: {base}")

    prompts: Dict[str, str] = {}
    for key, filename in DEFAULT_REVIEWER_PROMPT_FILES.items():
        path = base / filename
        if not path.exists():
            raise FileNotFoundError(f"Reviewer prompt file not found: {path}")
        prompts[key] = path.read_text(encoding="utf-8")
    return prompts
