from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import requests


DEFAULT_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_MODELS_CACHE: Dict[str, List[Dict[str, Any]]] = {}


def _normalize_models_url(base_url: Optional[str]) -> str:
    if not base_url:
        return DEFAULT_OPENROUTER_MODELS_URL
    base = base_url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base.endswith("/api/v1"):
        return f"{base}/models"
    if base.endswith("/api/v1/models"):
        return base
    return f"{base}/api/v1/models"


def _to_decimal(x: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    if x is None:
        return default
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return default


def fetch_models(
    api_key: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
    timeout_s: int = 30,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    models_url = _normalize_models_url(base_url)
    if use_cache and models_url in _MODELS_CACHE:
        return list(_MODELS_CACHE[models_url])

    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = requests.get(models_url, headers=headers, timeout=timeout_s)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response shape. Top-level keys: {list(payload.keys())}")
    _MODELS_CACHE[models_url] = data
    return list(data)


def find_model(models: List[Dict[str, Any]], query: str, *, allow_fuzzy: bool = True) -> Dict[str, Any]:
    q = query.strip().lower()

    for m in models:
        if str(m.get("id", "")).lower() == q or str(m.get("name", "")).lower() == q:
            return m

    if not allow_fuzzy:
        raise KeyError(f"Model '{query}' not found (by id/name).")

    candidates = []
    for m in models:
        mid = str(m.get("id", "")).lower()
        mname = str(m.get("name", "")).lower()
        if q in mid or q in mname:
            candidates.append(m)

    if not candidates:
        raise KeyError(f"Model '{query}' not found (by id/name).")

    candidates.sort(key=lambda m: (len(str(m.get("id", ""))), str(m.get("id", ""))))
    return candidates[0]


def get_model_token_pricing(
    model_id: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout_s: int = 30,
    allow_fuzzy: bool = False,
) -> Dict[str, Any]:
    models = fetch_models(api_key=api_key, base_url=base_url, timeout_s=timeout_s)
    model = find_model(models, model_id, allow_fuzzy=allow_fuzzy)
    pricing = model.get("pricing") or {}
    if not isinstance(pricing, dict):
        pricing = {}

    prompt_price = _to_decimal(pricing.get("prompt"))
    completion_price = _to_decimal(pricing.get("completion"))
    per_million = Decimal("1000000")

    return {
        "model_id": model.get("id"),
        "model_name": model.get("name") or model.get("id"),
        "pricing": pricing,
        "prompt_price_per_token": float(prompt_price) if prompt_price is not None else None,
        "completion_price_per_token": float(completion_price) if completion_price is not None else None,
        "prompt_price_per_million": float(prompt_price * per_million) if prompt_price is not None else None,
        "completion_price_per_million": float(completion_price * per_million)
        if completion_price is not None
        else None,
    }


def estimate_cost(
    model: Dict[str, Any],
    *,
    input_tokens_per_call: int,
    output_tokens_per_call: int,
    api_calls: int,
    include_free_models: bool = False,
    extra_units_per_call: Optional[Dict[str, Decimal]] = None,
) -> Dict[str, Any]:
    pricing = model.get("pricing") or {}
    if not isinstance(pricing, dict):
        pricing = {}

    units_per_call: Dict[str, Decimal] = {
        "prompt": Decimal(input_tokens_per_call),
        "completion": Decimal(output_tokens_per_call),
        "request": Decimal(1),
    }
    if extra_units_per_call:
        units_per_call.update(extra_units_per_call)

    prompt_price = _to_decimal(pricing.get("prompt"), default=Decimal("0")) or Decimal("0")
    completion_price = _to_decimal(pricing.get("completion"), default=Decimal("0")) or Decimal("0")

    if not include_free_models and (prompt_price <= 0 and completion_price <= 0):
        return {
            "keep": False,
            "reason": "free_or_missing_pricing",
            "id": model.get("id"),
            "name": model.get("name") or model.get("id"),
        }

    calls = Decimal(api_calls)
    breakdown: Dict[str, Dict[str, Any]] = {}
    total_cost = Decimal("0")

    for key, raw_price in pricing.items():
        price = _to_decimal(raw_price, default=Decimal("0")) or Decimal("0")
        units_total = units_per_call.get(key, Decimal("0")) * calls
        cost = price * units_total
        breakdown[key] = {
            "price_per_unit_usd": price,
            "units_total": units_total,
            "cost_usd": cost,
        }
        total_cost += cost

    input_cost = prompt_price * Decimal(input_tokens_per_call) * calls
    output_cost = completion_price * Decimal(output_tokens_per_call) * calls

    return {
        "keep": True,
        "id": model.get("id"),
        "name": model.get("name") or model.get("id"),
        "pricing": pricing,
        "api_calls": api_calls,
        "input_tokens_per_call": input_tokens_per_call,
        "output_tokens_per_call": output_tokens_per_call,
        "input_cost_usd": input_cost,
        "output_cost_usd": output_cost,
        "total_cost_usd": total_cost,
        "breakdown": breakdown,
    }
