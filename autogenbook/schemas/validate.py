from __future__ import annotations

import json
from typing import Any, Type

from pydantic import ValidationError


def validate_or_raise(model_cls: Type[Any], data: Any) -> Any:
    return model_cls.model_validate(data)


def format_validation_error(e: ValidationError, max_lines: int = 25) -> str:
    lines = []
    for err in e.errors():
        loc = ".".join(str(part) for part in err.get("loc", []))
        msg = err.get("msg", "validation error")
        lines.append(f"{loc}: {msg}")
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def json_schema_snippet(model_cls: Type[Any], max_chars: int = 2000) -> str:
    schema = model_cls.model_json_schema()
    raw = json.dumps(schema, ensure_ascii=False, indent=2)
    if len(raw) <= max_chars:
        return raw
    return raw[: max(0, max_chars - 3)] + "..."
