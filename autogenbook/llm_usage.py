from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from openrouter_llm import OpenRouterLLM
from autogenbook.openrouter_usage import copy_totals, merge_cost_totals, merge_usage_totals


_OPENROUTER_USAGE_TOTALS: Dict[str, Any] = {"overall": {}, "by_modality": {}, "by_model": {}}


def _ensure_totals(bucket: Dict[str, Any], key: str) -> Dict[str, Any]:
    if key not in bucket:
        bucket[key] = {}
    return bucket[key]


def record_openrouter_usage(
    *,
    modality: str,
    model: str,
    usage_breakdown: Optional[Dict[str, Any]],
    cost_breakdown: Optional[Dict[str, Any]],
) -> None:
    usage_breakdown = usage_breakdown or {}
    cost_breakdown = cost_breakdown or {}
    overall = _ensure_totals(_OPENROUTER_USAGE_TOTALS, "overall")
    by_modality = _ensure_totals(_OPENROUTER_USAGE_TOTALS, "by_modality")
    by_model = _ensure_totals(_OPENROUTER_USAGE_TOTALS, "by_model")

    merge_usage_totals(overall, usage_breakdown)
    merge_cost_totals(overall, cost_breakdown)

    modality_bucket = _ensure_totals(by_modality, modality)
    merge_usage_totals(modality_bucket, usage_breakdown)
    merge_cost_totals(modality_bucket, cost_breakdown)

    model_bucket = _ensure_totals(by_model, model or "unknown")
    merge_usage_totals(model_bucket, usage_breakdown)
    merge_cost_totals(model_bucket, cost_breakdown)


def get_openrouter_usage_totals() -> Dict[str, Any]:
    return copy_totals(_OPENROUTER_USAGE_TOTALS)


def serialize_args(args: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in vars(args).items():
        if key == "run_ctx":
            continue
        if isinstance(value, Path):
            out[key] = str(value)
        else:
            try:
                json.dumps(value)
                out[key] = value
            except TypeError:
                out[key] = str(value)
    return out


def write_run_meta(
    *,
    run_ctx: Any,
    args: Any,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    error: Optional[str],
    models: Optional[Dict[str, Any]] = None,
    token_totals: Optional[Dict[str, Any]] = None,
    cost_totals_usd: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    meta = {
        "run_id": run_ctx.run_id,
        "mode": run_ctx.mode,
        "out_dir": str(run_ctx.out_dir),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_sec": (finished_at - started_at).total_seconds(),
        "status": status,
        "error": error,
        "args": serialize_args(args),
        "models": models or {},
        "token_totals": token_totals or {},
        "cost_totals_usd": cost_totals_usd or {},
        "run_context": run_ctx.to_json(),
        "openrouter_usage_totals": get_openrouter_usage_totals(),
    }
    if extra:
        meta["extra"] = extra
    run_ctx.out_dir.mkdir(parents=True, exist_ok=True)
    (run_ctx.out_dir / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def log_usage(
    out_dir: Path,
    label: str,
    llm: OpenRouterLLM,
    usage: Optional[Dict[str, int]] = None,
    extra: Optional[Dict[str, Any]] = None,
    *,
    modality: str = "text",
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "llm_usage.jsonl"
    usage_breakdown = llm.get_last_usage_breakdown()
    cost_breakdown = llm.get_last_cost_breakdown()
    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "label": label,
        "model": llm.config.model,
        "modality": modality,
        "usage": usage or llm.get_last_usage(),
        "usage_breakdown": usage_breakdown,
        "cost_breakdown": cost_breakdown,
        "cost_usd": llm.get_last_cost_usd(),
        "total_cost_usd": llm.get_total_cost_usd(),
        "total_tokens": llm.get_total_tokens(),
        "extra": extra or {},
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    record_openrouter_usage(
        modality=modality,
        model=llm.config.model,
        usage_breakdown=usage_breakdown,
        cost_breakdown=cost_breakdown,
    )


def log_openrouter_event(
    out_dir: Path,
    *,
    label: str,
    model: str,
    modality: str,
    usage_breakdown: Optional[Dict[str, Any]] = None,
    cost_breakdown: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "llm_usage.jsonl"
    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "label": label,
        "model": model,
        "modality": modality,
        "usage": {},
        "usage_breakdown": usage_breakdown or {},
        "cost_breakdown": cost_breakdown or {},
        "cost_usd": (cost_breakdown or {}).get("total_cost_usd"),
        "total_cost_usd": None,
        "total_tokens": None,
        "extra": extra or {},
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    record_openrouter_usage(
        modality=modality,
        model=model,
        usage_breakdown=usage_breakdown,
        cost_breakdown=cost_breakdown,
    )
