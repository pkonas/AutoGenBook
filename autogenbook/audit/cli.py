from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Set

from .latex_auditor import AuditorConfig, audit_latex
from .types import AuditSeverity


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _load_known_cites(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    if path.suffix.lower() == ".bib":
        return _parse_bib_keys(_read_text(path))
    data = json.loads(_read_text(path))
    if isinstance(data, list):
        return {str(x) for x in data}
    if isinstance(data, dict):
        keys = data.get("cite_keys") or data.get("known_cite_keys") or data.get("entries")
        if isinstance(keys, list):
            return {str(x) for x in keys}
        if isinstance(keys, dict):
            return {str(k) for k in keys.keys()}
    return set()


def _load_known_rids(path: Path) -> Set[str]:
    if not path or not path.exists():
        return set()
    data = json.loads(_read_text(path))
    if isinstance(data, list):
        return {str(x) for x in data}
    if isinstance(data, dict):
        keys = data.get("rids") or data.get("known_rids")
        if isinstance(keys, list):
            return {str(x) for x in keys}
    return set()


def _parse_bib_keys(text: str) -> Set[str]:
    pattern = re.compile(r"@\w+\{([^,]+),")
    return {m.group(1).strip() for m in pattern.finditer(text) if m.group(1).strip()}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit LaTeX for grounding and references.")
    parser.add_argument("--tex", required=True, help="Path to .tex file to audit.")
    parser.add_argument("--known-cites", required=False, help="Path to JSON or .bib file of known cite keys.")
    parser.add_argument("--known-rids", required=False, help="Path to JSON file with known RIDs.")
    parser.add_argument("--mode", choices=["off", "warn", "strict"], default="warn")
    parser.add_argument("--window-chars", type=int, default=600)
    parser.add_argument("--doc-kind", default="paper", choices=["book", "paper", "scientist"])
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(list(argv) if argv is not None else None)

    tex_path = Path(args.tex).expanduser().resolve()
    tex_text = _read_text(tex_path)
    known_cites = set()
    if args.known_cites:
        known_cites = _load_known_cites(Path(args.known_cites).expanduser().resolve())
    known_rids = set()
    if args.known_rids:
        known_rids = _load_known_rids(Path(args.known_rids).expanduser().resolve())

    report = audit_latex(
        tex_path=tex_path,
        tex_text=tex_text,
        doc_kind=args.doc_kind,
        known_cite_keys=known_cites,
        known_rids=known_rids,
        project_root=Path(args.project_root).expanduser().resolve(),
        config=AuditorConfig(
            enabled=True,
            mode=args.mode,
            evidence_window_chars=int(args.window_chars),
        ),
    )
    print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))

    errors = report.counts_by_severity.get(AuditSeverity.ERROR.value, 0)
    warnings = report.counts_by_severity.get(AuditSeverity.WARNING.value, 0)
    if args.mode == "strict" and errors:
        return 4
    if warnings and errors == 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
