from __future__ import annotations

import argparse
import sys
from glob import glob
from pathlib import Path
from typing import Iterable, List

import book_builder


def _expand_paths(values: List[str]) -> List[Path]:
    paths: List[Path] = []
    for value in values:
        if any(ch in value for ch in "*?[]"):
            for match in glob(value, recursive=True):
                paths.append(Path(match))
            continue
        p = Path(value)
        if p.is_dir():
            paths.extend(p.rglob("*.tex"))
            continue
        paths.append(p)
    return paths


def _repair_text(text: str, out_dir: Path) -> str:
    text = book_builder._normalize_unicode_math_symbols(text)
    text = book_builder._escape_hash_outside_math(text)
    text = book_builder._fix_linebreak_before_hash(text)
    text = book_builder._escape_hash_outside_math(text)
    text = book_builder._fix_linebreak_before_hash(text)
    text = book_builder._close_unmatched_math_envs(text)
    text = book_builder._strip_blank_lines_in_math_envs(text)
    text = book_builder._replace_nonascii_lstlisting(text)
    text = book_builder._sanitize_lstlisting_language(text)
    text = book_builder._strip_linebreaks_at_line_start(text)
    text = book_builder._strip_linebreaks_after_item_label(text)
    text = book_builder._fix_escaped_quotes(text)
    text = book_builder._replace_ascii_quotes(text)
    text = book_builder._fix_tabular_alignment(text)
    text = book_builder._apply_iso690_citations(text, None, out_dir)
    return text


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    return book_builder._decode_text_bytes(raw)


def repair_tex(path: Path, out_dir: Path, backup: bool) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    text = _read_text(path)
    repaired = _repair_text(text, out_dir)
    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        backup_path.write_text(text, encoding="utf-8")
    path.write_text(repaired, encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair LaTeX files in place using AutoGenBook sanitizers."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Path(s), globs, or directories containing .tex files.",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory containing kb_sources.json/refs.bib (default: tex parent).",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak backups.",
    )
    args = parser.parse_args(argv)

    paths = _expand_paths(list(args.paths))
    if not paths:
        print("No .tex files found.", file=sys.stderr)
        return 1

    for path in paths:
        if path.suffix.lower() != ".tex":
            continue
        out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else path.parent
        repair_tex(path, out_dir, backup=not args.no_backup)
        print(f"Repaired: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
