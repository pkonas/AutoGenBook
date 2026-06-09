from __future__ import annotations

from typing import Iterable


def ensure_non_empty_list(values: Iterable[object]) -> list[object]:
    items = list(values)
    if not items:
        raise ValueError("List must be non-empty.")
    return items


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def validate_sum_approx(values: Iterable[float], target: float, tolerance: float) -> bool:
    total = sum(values)
    return abs(total - target) <= tolerance
