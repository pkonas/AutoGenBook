from __future__ import annotations

import argparse
import os
from decimal import Decimal
from typing import Any, Dict, List

from autogenbook.openrouter_pricing import (
    estimate_cost,
    fetch_models,
    find_model,
)


def _to_decimal(x: Any, default: Decimal = Decimal("0")) -> Decimal:
    if x is None:
        return default
    try:
        return Decimal(str(x))
    except Exception:
        return default


def format_usd(x: Decimal) -> str:
    return f"${x:.6f}"


def format_usd_per_million_tokens(price_per_token: Decimal) -> str:
    return f"${(price_per_token * Decimal('1000000')):.6f} / 1M tokens"


def parse_extra_kv(pairs: List[str]) -> Dict[str, Decimal]:
    out: Dict[str, Decimal] = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"Invalid --extra '{p}', expected key=value")
        k, v = p.split("=", 1)
        out[k.strip()] = _to_decimal(v.strip())
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="OpenRouter price fetcher + token cost calculator.")
    ap.add_argument("--model", required=False, help="Model id or name (exact or substring).")
    ap.add_argument("--input-tokens", type=int, default=16384, help="Input tokens per call")
    ap.add_argument("--output-tokens", type=int, default=500, help="Output tokens per call")
    ap.add_argument("--api-calls", type=int, default=1500, help="Number of calls")
    ap.add_argument("--include-free", action="store_true", help="Include free models (0-priced)")
    ap.add_argument("--limit", type=int, default=25, help="When listing, limit number of rows shown")
    ap.add_argument(
        "--extra",
        action="append",
        default=[],
        help="Extra per-call units for pricing keys: --extra web_search=1 --extra internal_reasoning=500",
    )
    ap.add_argument("--api-key", default=os.getenv("OPENROUTER_API_KEY"), help="OpenRouter API key")
    ap.add_argument(
        "--base-url",
        default=os.getenv("OPENROUTER_BASE_URL"),
        help="Override OpenRouter base URL (default: https://openrouter.ai/api/v1)",
    )
    args = ap.parse_args()

    models = fetch_models(api_key=args.api_key, base_url=args.base_url)
    extra_units = parse_extra_kv(args.extra)

    if args.model:
        m = find_model(models, args.model, allow_fuzzy=True)
        result = estimate_cost(
            m,
            input_tokens_per_call=args.input_tokens,
            output_tokens_per_call=args.output_tokens,
            api_calls=args.api_calls,
            include_free_models=True,
            extra_units_per_call=extra_units,
        )

        print(f"Model: {result['name']}")
        print(f"ID:    {result['id']}")
        print()

        pricing = result.get("pricing", {}) or {}
        prompt = _to_decimal(pricing.get("prompt"))
        completion = _to_decimal(pricing.get("completion"))

        print("Per-unit pricing (as returned by OpenRouter):")
        for k in sorted(pricing.keys()):
            print(f"  - {k}: {pricing[k]}")
        print()

        if prompt != 0:
            print(f"Input  (prompt):     {format_usd_per_million_tokens(prompt)}")
        else:
            print("Input  (prompt):     $0 (free or not priced)")
        if completion != 0:
            print(f"Output (completion): {format_usd_per_million_tokens(completion)}")
        else:
            print("Output (completion): $0 (free or not priced)")
        print()

        print(f"Cost estimate for {args.api_calls} calls:")
        print(f"  - Input cost:  {format_usd(result['input_cost_usd'])}")
        print(f"  - Output cost: {format_usd(result['output_cost_usd'])}")
        print(
            "  - Total cost (incl. any extra priced keys present): "
            f"{format_usd(result['total_cost_usd'])}"
        )
        print()

        print("Breakdown (priced keys present on this model):")
        breakdown = result["breakdown"]
        for k in sorted(breakdown.keys()):
            row = breakdown[k]
            print(
                f"  - {k:18s} units={row['units_total']}  "
                f"price={row['price_per_unit_usd']}  cost={format_usd(row['cost_usd'])}"
            )
    else:
        rows = []
        for m in models:
            r = estimate_cost(
                m,
                input_tokens_per_call=args.input_tokens,
                output_tokens_per_call=args.output_tokens,
                api_calls=args.api_calls,
                include_free_models=args.include_free,
                extra_units_per_call=extra_units,
            )
            if r.get("keep"):
                rows.append(r)

        rows.sort(key=lambda x: x["total_cost_usd"])

        print(
            f"Listing models sorted by total cost (calls={args.api_calls}, "
            f"in={args.input_tokens}, out={args.output_tokens})"
        )
        print(f"Showing top {min(args.limit, len(rows))} rows\n")

        header = f"{'TOTAL($)':>12}  {'INPUT($)':>12}  {'OUTPUT($)':>12}  {'MODEL ID':40s}  NAME"
        print(header)
        print("-" * len(header))

        for r in rows[: args.limit]:
            print(
                f"{str(r['total_cost_usd']):>12s}  "
                f"{str(r['input_cost_usd']):>12s}  "
                f"{str(r['output_cost_usd']):>12s}  "
                f"{str(r['id']):40.40s}  {r['name']}"
            )


if __name__ == "__main__":
    main()
