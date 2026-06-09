from __future__ import annotations

from typing import Any, Dict, Type

from .base import BaseAgent

_REGISTRY: Dict[str, Type[BaseAgent]] = {}


def register_agent(name: str):
    def _wrap(cls: Type[BaseAgent]) -> Type[BaseAgent]:
        _REGISTRY[name] = cls
        return cls

    return _wrap


def get_agent(name: str) -> Type[BaseAgent]:
    if name not in _REGISTRY:
        raise KeyError(f"Agent '{name}' is not registered.")
    return _REGISTRY[name]
