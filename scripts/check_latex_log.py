#!/usr/bin/env python
from __future__ import annotations

import re
import sys
from pathlib import Path


PATTERNS = [
    (re.compile(r"! LaTeX Error:"), "LaTeX Error"),
    (re.compile(r"Missing character:"), "Missing character"),
    (re.compile(r"Overfull \\\\hbox"), "Overfull hbox"),
    (re.compile(r"Environment [^ ]+ undefined"), "Undefined environment"),
    (re.compile(r"hyperref:.*duplicate destination", re.IGNORECASE), "Duplicate hyperref destination"),
]


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: check_latex_log.py <file.log>", file=sys.stderr)
        return 2

    log_path = Path(sys.argv[1])
    if not log_path.exists():
        print(f"Log file not found: {log_path}", file=sys.stderr)
        return 2

    text = log_path.read_text(encoding="utf-8", errors="ignore")
    failures = []
    for pattern, label in PATTERNS:
        if pattern.search(text):
            failures.append(label)

    if failures:
        print("Log gate failed. Found:", ", ".join(sorted(set(failures))), file=sys.stderr)
        return 1

    print("Log gate OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
