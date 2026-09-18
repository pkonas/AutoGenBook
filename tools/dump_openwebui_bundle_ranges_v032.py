#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS = sorted((ROOT / "integration").glob("openwebui_bundle.part*.b64"))
RANGES = {
    "companion/src/autogenbook_companion/config.py": [(1, 240)],
    "companion/src/autogenbook_companion/service.py": [(1, 110), (240, 580)],
    "companion/src/autogenbook_companion/models.py": [(1, 240)],
    "companion/src/autogenbook_companion/database.py": [(220, 430)],
    "installer/install.py": [(1, 360)],
    "integrations/openwebui/autogenbook_pipe.py": [(1, 120), (480, 760), (760, 980)],
}


def main() -> int:
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in PARTS)
    payload = base64.b64decode(encoded, validate=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        for path, ranges in RANGES.items():
            member = archive.getmember(path)
            handle = archive.extractfile(member)
            if handle is None:
                raise RuntimeError(path)
            lines = handle.read().decode("utf-8-sig").splitlines()
            for start, end in ranges:
                print(f"\n===== {path}:{start}-{end} / {len(lines)} =====")
                for number in range(max(1, start), min(end, len(lines)) + 1):
                    print(f"{number:05d}: {lines[number - 1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
