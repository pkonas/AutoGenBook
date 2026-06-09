from __future__ import annotations

import copy
from typing import Any, Dict, Optional


_KNOWN_INPUT_TOKEN_KEYS = ("prompt_tokens", "input_tokens")
_KNOWN_OUTPUT_TOKEN_KEYS = ("completion_tokens", "output_tokens")
_KNOWN_TOTAL_TOKEN_KEYS = ("total_tokens",)

_KNOWN_INPUT_COST_KEYS = ("prompt_cost", "input_cost", "input_cost_usd")
_KNOWN_OUTPUT_COST_KEYS = ("completion_cost", "output_cost", "output_cost_usd")
_KNOWN_TOTAL_COST_KEYS = ("total_cost", "cost", "total_cost_usd")


def _to_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except Exception:
            return None
    return None


def _get_first_number(data: Dict[str, Any], keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in data:
            value = _to_number(data.get(key))
            if value is not None:
                return value
    return None


def _normalize_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not headers:
        return {}
    normalized: Dict[str, str] = {}
    for key, value in headers.items():
        if key is None:
            continue
        normalized[str(key).lower()] = str(value)
    return normalized


def extract_usage_breakdown(
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    usage = {}
    if payload and isinstance(payload.get("usage"), dict):
        usage = dict(payload.get("usage") or {})

    header_map = _normalize_headers(headers)
    header_usage = {}
    for key, value in header_map.items():
        if not key.startswith("x-openrouter-"):
            continue
        header_usage[key] = value

    input_tokens = _get_first_number(usage, _KNOWN_INPUT_TOKEN_KEYS)
    output_tokens = _get_first_number(usage, _KNOWN_OUTPUT_TOKEN_KEYS)
    total_tokens = _get_first_number(usage, _KNOWN_TOTAL_TOKEN_KEYS)

    if input_tokens is None:
        input_tokens = _to_number(
            header_usage.get("x-openrouter-input-tokens")
            or header_usage.get("x-openrouter-prompt-tokens")
        )
    if output_tokens is None:
        output_tokens = _to_number(
            header_usage.get("x-openrouter-output-tokens")
            or header_usage.get("x-openrouter-completion-tokens")
        )
    if total_tokens is None:
        total_tokens = _to_number(header_usage.get("x-openrouter-total-tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    details_in = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    details_out = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}

    breakdown: Dict[str, Any] = {
        "input_tokens": int(input_tokens) if input_tokens is not None else None,
        "output_tokens": int(output_tokens) if output_tokens is not None else None,
        "total_tokens": int(total_tokens) if total_tokens is not None else None,
        "input_tokens_details": details_in if isinstance(details_in, dict) else {},
        "output_tokens_details": details_out if isinstance(details_out, dict) else {},
        "source": "response" if usage else ("headers" if header_usage else "none"),
    }
    return breakdown


def extract_cost_breakdown(
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    usage = {}
    if payload and isinstance(payload.get("usage"), dict):
        usage = dict(payload.get("usage") or {})
    top_level = payload if isinstance(payload, dict) else {}

    header_map = _normalize_headers(headers)
    header_cost = {}
    for key, value in header_map.items():
        if not key.startswith("x-openrouter-"):
            continue
        header_cost[key] = value

    input_cost = _get_first_number(usage, _KNOWN_INPUT_COST_KEYS)
    output_cost = _get_first_number(usage, _KNOWN_OUTPUT_COST_KEYS)
    total_cost = _get_first_number(usage, _KNOWN_TOTAL_COST_KEYS)
    if total_cost is None:
        total_cost = _get_first_number(top_level, _KNOWN_TOTAL_COST_KEYS)

    if input_cost is None:
        input_cost = _to_number(header_cost.get("x-openrouter-input-cost"))
    if output_cost is None:
        output_cost = _to_number(header_cost.get("x-openrouter-output-cost"))
    if total_cost is None:
        total_cost = _to_number(header_cost.get("x-openrouter-total-cost"))
    if total_cost is None:
        total_cost = _to_number(header_cost.get("x-openrouter-cost"))
    if total_cost is None and input_cost is not None and output_cost is not None:
        total_cost = input_cost + output_cost

    breakdown: Dict[str, Any] = {
        "input_cost_usd": input_cost,
        "output_cost_usd": output_cost,
        "total_cost_usd": total_cost,
        "source": "response" if usage else ("headers" if header_cost else "none"),
    }
    return breakdown


def merge_usage_totals(total: Dict[str, Any], add: Dict[str, Any]) -> Dict[str, Any]:
    def _add_value(key: str) -> None:
        val = add.get(key)
        if val is None:
            return
        total[key] = total.get(key, 0) + int(val)

    _add_value("input_tokens")
    _add_value("output_tokens")
    _add_value("total_tokens")

    for detail_key in ("input_tokens_details", "output_tokens_details"):
        details = add.get(detail_key)
        if not isinstance(details, dict):
            continue
        total.setdefault(detail_key, {})
        for d_key, d_val in details.items():
            if d_val is None:
                continue
            try:
                total[detail_key][d_key] = total[detail_key].get(d_key, 0) + int(d_val)
            except Exception:
                continue
    return total


def merge_cost_totals(total: Dict[str, Any], add: Dict[str, Any]) -> Dict[str, Any]:
    def _add_value(key: str) -> None:
        val = add.get(key)
        if val is None:
            return
        total[key] = float(total.get(key, 0.0)) + float(val)

    _add_value("input_cost_usd")
    _add_value("output_cost_usd")
    _add_value("total_cost_usd")
    return total


def copy_totals(data: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(data)
