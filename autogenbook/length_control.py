from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from openrouter_llm import OpenRouterLLM
from autogenbook.llm_usage import log_usage
from autogenbook.prompts.agent_prompts import render
from autogenbook.prompts.registry import get_prompt


DEFAULT_LINES_PER_PAGE = 40


@dataclass
class LengthCheck:
    target_lines: int
    actual_lines: int
    min_lines: int
    max_lines: int
    within_range: bool


def count_effective_lines(tex: str) -> int:
    count = 0
    for line in tex.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("%"):
            continue
        count += 1
    return count


def enforce_section_length(
    llm: OpenRouterLLM,
    tex: str,
    *,
    target_pages: float,
    out_dir: Path,
    label: str,
    lines_per_page: int = DEFAULT_LINES_PER_PAGE,
    tolerance: float = 0.25,
    max_passes: int = 2,
) -> Tuple[str, LengthCheck]:
    target_lines = max(1, int(round(target_pages * lines_per_page)))
    min_lines = max(1, int(round(target_lines * (1.0 - tolerance))))
    max_lines = max(1, int(round(target_lines * (1.0 + tolerance))))

    def _check(text: str) -> LengthCheck:
        actual = count_effective_lines(text)
        return LengthCheck(
            target_lines=target_lines,
            actual_lines=actual,
            min_lines=min_lines,
            max_lines=max_lines,
            within_range=min_lines <= actual <= max_lines,
        )

    check = _check(tex)
    if check.within_range:
        return tex, check

    system_prompt = get_prompt("length_control_system")
    user_template = get_prompt("length_control_user")

    current = tex
    for attempt in range(1, max_passes + 1):
        direction = "shorten" if check.actual_lines > check.max_lines else "expand"
        user_prompt = render(
            user_template,
            direction=direction,
            target_lines=target_lines,
            min_lines=min_lines,
            max_lines=max_lines,
            section=current,
        )
        revised = llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            allow_tools=False,
        )
        usage = llm.get_last_usage()
        if usage:
            print(llm.format_usage_line(usage, label=f"{label}_len"))
            log_usage(out_dir, f"{label}_len", llm, usage, {"attempt": attempt})
        current = revised.strip()
        check = _check(current)
        if check.within_range:
            break

    return current, check
