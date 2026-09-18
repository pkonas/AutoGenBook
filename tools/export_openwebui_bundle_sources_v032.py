#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS = sorted((ROOT / "integration").glob("openwebui_bundle.part*.b64"))
TARGETS = {
    "companion/src/autogenbook_companion/config.py",
    "companion/src/autogenbook_companion/service.py",
    "companion/src/autogenbook_companion/models.py",
    "companion/src/autogenbook_companion/database.py",
    "installer/install.py",
    "integrations/openwebui/autogenbook_pipe.py",
}


def main() -> int:
    encoded = "".join(path.read_text(encoding="ascii").strip() for path in PARTS)
    payload = base64.b64decode(encoded, validate=True)
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        for member in archive.getmembers():
            if member.name not in TARGETS:
                continue
            handle = archive.extractfile(member)
            if handle is not None:
                files[member.name] = handle.read().decode("utf-8-sig")
    missing = sorted(TARGETS - files.keys())
    if missing:
        raise RuntimeError(f"Missing bundle files: {missing}")
    output = ROOT / "build" / "openwebui-bundle-sources-v0.3.2.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"files": files}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
