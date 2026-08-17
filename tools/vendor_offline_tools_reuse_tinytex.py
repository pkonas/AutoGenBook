from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from tools import vendor_offline_tools as vendor


def reuse_pre_staged_tinytex(source: Path, destination: Path, *, platform_name: str) -> None:
    del source, platform_name
    if not destination.is_dir():
        raise RuntimeError(f"Pre-staged TinyTeX tree is missing: {destination}")
    required = ["lualatex.exe", "pdflatex.exe", "bibtex.exe"]
    found = {path.name.lower() for path in destination.rglob("*.exe")}
    missing = [name for name in required if name not in found]
    if missing:
        raise RuntimeError(f"Pre-staged TinyTeX is incomplete; missing: {missing}")
    print(f"Reusing pre-staged TinyTeX at {destination}", flush=True)


def main() -> int:
    diagnostic = Path("dist/windows-vendor-diagnostic.json").resolve()
    try:
        vendor.copy_tree = reuse_pre_staged_tinytex
        return vendor.main()
    except BaseException as exc:
        diagnostic.parent.mkdir(parents=True, exist_ok=True)
        diagnostic.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "argv": sys.argv,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(diagnostic.read_text(encoding="utf-8"), file=sys.stderr)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
