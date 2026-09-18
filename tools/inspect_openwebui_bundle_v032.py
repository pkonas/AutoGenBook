#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import re
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS = sorted((ROOT / "integration").glob("openwebui_bundle.part*.b64"))
TERMS = [
    "def _publish_artifact_to_openwebui",
    "def _file_payload",
    "download_url",
    "Invalid companion token",
    "download_sign",
    "expires",
    "expiry",
    "bridge.json",
    "--port",
    "port=0",
    "FileForm(",
    "upload_file_handler",
    "PERSIST_OUTPUTS_TO_OPENWEBUI",
    "artifact",
]


def snippets(text: str, path: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    found: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for term in TERMS:
        for index, line in enumerate(lines):
            if term.casefold() not in line.casefold():
                continue
            key = (index, term)
            if key in seen:
                continue
            seen.add(key)
            start = max(0, index - 12)
            end = min(len(lines), index + 30)
            found.append(
                {
                    "path": path,
                    "term": term,
                    "line": index + 1,
                    "snippet": "\n".join(
                        f"{number + 1:05d}: {lines[number]}" for number in range(start, end)
                    ),
                }
            )
    return found


def main() -> int:
    if not PARTS:
        raise SystemExit("No integration bundle parts found")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in PARTS)
    payload = base64.b64decode(encoded, validate=True)
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="agb-v032-inspect-") as temp:
        archive_path = Path(temp) / "bundle.zip"
        archive_path.write_bytes(payload)
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.lower().endswith((".py", ".cmd", ".ps1", ".sh", ".json")):
                    continue
                try:
                    text = archive.read(info).decode("utf-8-sig")
                except Exception:
                    continue
                rows = snippets(text, info.filename)
                if rows:
                    results.extend(rows)
    report = {
        "parts": len(PARTS),
        "bundle_bytes": len(payload),
        "matches": results,
    }
    output = ROOT / "build" / "openwebui-bundle-inspection-v0.3.2.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
