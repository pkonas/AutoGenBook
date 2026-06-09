from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _run(cmd: list[str]) -> None:
    env = dict(os.environ)
    env.setdefault("AUTOGENBOOK_NONINTERACTIVE", "1")
    env.setdefault("AUTOGENBOOK_ASSUME_YES", "1")
    env.setdefault("MCP_GATEWAY_ENABLE", "0")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("AUTOGENBOOK_FORCE_MINI_MODEL", "1")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)


def _assert_any(path: Path, pattern: str) -> None:
    if not any(path.rglob(pattern)):
        raise SystemExit(f"Expected artifacts matching '{pattern}' under {path}")


def main() -> int:
    fast_mode = os.environ.get("AUTOGENBOOK_SMOKE_FAST", "").strip().lower() in {"1", "true", "yes", "on"}
    if not os.environ.get("OPENROUTER_API_KEY") and not fast_mode:
        print("OPENROUTER_API_KEY not set; skipping smoke test.")
        return 0

    repo_root = Path(__file__).resolve().parents[1]
    main_py = repo_root / "main.py"
    base_out = repo_root / "out_smoke"
    if base_out.exists():
        try:
            shutil.rmtree(base_out)
        except PermissionError:
            stamp = int(time.time())
            base_out = repo_root / f"out_smoke_{stamp}"
    base_out.mkdir(parents=True, exist_ok=True)

    input_path = base_out / "input.txt"
    input_path.write_text(
        "Název: Mini průvodce AI ve výuce\n"
        "Obsah: Krátká ukázka struktury knihy a článku.\n"
        "Kapitoly:\n"
        "1. Úvod\n"
        "2. Praktické příklady\n"
        "Počet stran: 2\n",
        encoding="utf-8",
    )

    if fast_mode:
        _run([sys.executable, "-m", "autogenbook.smoke_prompts"])
        _run([sys.executable, "-m", "autogenbook.schemas.smoke"])
        _run([sys.executable, "-m", "autogenbook.smoke_proposal"])
        print("Smoke test OK (fast).")
        return 0

    has_latex = bool(shutil.which("lualatex") or shutil.which("pdflatex"))
    no_pdf_flag = [] if has_latex else ["--no-pdf"]

    book_out = base_out / "book"
    _run(
        [
            sys.executable,
            str(main_py),
            "--mode",
            "book",
            "--input",
            str(input_path),
            "--out-dir",
            str(book_out),
            "--use-txt",
            "--no-md",
            *(["--max-iters", "1"] if fast_mode else []),
            *no_pdf_flag,
        ]
    )
    _assert_any(book_out / "sections", "*.tex")

    paper_out = base_out / "paper"
    _run(
        [
            sys.executable,
            str(main_py),
            "--mode",
            "paper",
            "--input",
            str(input_path),
            "--out-dir",
            str(paper_out),
            "--use-txt",
            "--no-md",
            *(["--max-iters", "1"] if fast_mode else []),
            *no_pdf_flag,
        ]
    )
    _assert_any(paper_out / "sections", "*.tex")

    if not fast_mode:
        scientist_out = base_out / "scientist"
        _run(
            [
                sys.executable,
                str(main_py),
                "--mode",
                "scientist",
                "--out-dir",
                str(scientist_out),
                "--no-md",
                *no_pdf_flag,
            ]
        )
        _assert_any(scientist_out, "*.tex")
        _assert_any(scientist_out / "experiments", "metrics.json")
        if not (scientist_out / "review.json").exists():
            raise SystemExit("Expected review.json in scientist output.")

    print("Smoke test OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
