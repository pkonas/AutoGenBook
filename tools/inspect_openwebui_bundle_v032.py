#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import json
import tarfile
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
    "@app.get",
    "@router.get",
]
TEXT_SUFFIXES = (".py", ".cmd", ".ps1", ".sh", ".json", ".md", ".txt")


def snippets(text: str, path: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    found: list[dict[str, object]] = []
    for term in TERMS:
        term_matches = 0
        for index, line in enumerate(lines):
            if term.casefold() not in line.casefold():
                continue
            start = max(0, index - 10)
            end = min(len(lines), index + 26)
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
            term_matches += 1
            if term_matches >= 12:
                break
    return found


def archive_members(payload: bytes):
    stream = io.BytesIO(payload)
    if zipfile.is_zipfile(stream):
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    yield info.filename, archive.read(info)
        return
    stream.seek(0)
    if tarfile.is_tarfile(fileobj := stream):
        fileobj.seek(0)
        with tarfile.open(fileobj=fileobj, mode="r:*") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    yield member.name, handle.read()
        return
    raise RuntimeError(f"Unsupported bundle format; magic={payload[:16].hex()}")


def main() -> int:
    if not PARTS:
        raise SystemExit("No integration bundle parts found")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in PARTS)
    payload = base64.b64decode(encoded, validate=True)
    results: list[dict[str, object]] = []
    inventory: list[dict[str, object]] = []
    for name, data in archive_members(payload):
        inventory.append({"path": name, "size": len(data)})
        if not name.lower().endswith(TEXT_SUFFIXES):
            continue
        try:
            text = data.decode("utf-8-sig")
        except Exception:
            continue
        results.extend(snippets(text, name))
    report = {
        "parts": len(PARTS),
        "part_names": [part.name for part in PARTS],
        "bundle_bytes": len(payload),
        "magic": payload[:16].hex(),
        "inventory": inventory,
        "matches": results,
    }
    output = ROOT / "build" / "openwebui-bundle-inspection-v0.3.2.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
