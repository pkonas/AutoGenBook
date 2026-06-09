from __future__ import annotations

from typing import Dict, Optional

_PROMPT_REGISTRY: Dict[str, str] = {}
_PROMPT_MODE: Optional[str] = None


def set_prompt_registry(mode: str, prompts: Dict[str, str]) -> None:
    global _PROMPT_REGISTRY, _PROMPT_MODE
    _PROMPT_REGISTRY = dict(prompts)
    _PROMPT_MODE = mode


def get_prompt(name: str) -> str:
    if name not in _PROMPT_REGISTRY:
        raise KeyError(
            f"Prompt '{name}' not loaded. Call set_prompt_registry() for the active mode."
        )
    return _PROMPT_REGISTRY[name]


def get_prompt_optional(name: str) -> Optional[str]:
    return _PROMPT_REGISTRY.get(name)


def get_prompt_mode() -> Optional[str]:
    return _PROMPT_MODE
