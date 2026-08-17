from __future__ import annotations

from pathlib import Path


def main() -> int:
    path = Path(__file__).resolve().parents[1] / "build" / "build_release.py"
    source = path.read_text(encoding="utf-8")
    old = 'with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:'
    new = (
        'with zipfile.ZipFile(\n'
        '        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, strict_timestamps=False\n'
        '    ) as zf:'
    )
    if new in source:
        print("Archive timestamp compatibility patch already applied.")
        return 0
    if old not in source:
        raise RuntimeError("Expected zip writer implementation was not found; refusing an unsafe patch.")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    print("Applied ZIP timestamp compatibility patch for standalone Python files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
