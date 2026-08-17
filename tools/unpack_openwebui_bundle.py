from __future__ import annotations

import base64
import hashlib
import io
import tarfile
from pathlib import Path

EXPECTED_SHA256 = "e14938bafe3fcd9532a37fe7631e2fad8ad7cd1e6389afcfd0fa151ea7fd3e75"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parts = sorted((root / "integration").glob("openwebui_bundle.part*.b64"))
    if not parts:
        raise RuntimeError("Integration bundle parts were not found.")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in parts)
    payload = base64.b64decode(encoded, validate=True)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != EXPECTED_SHA256:
        raise RuntimeError(f"Integration bundle hash mismatch: {digest}")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            target = (root / member.name).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError as exc:
                raise RuntimeError(f"Unsafe bundle member: {member.name}") from exc
            if member.issym() or member.islnk():
                raise RuntimeError(f"Links are not allowed in the integration bundle: {member.name}")
        try:
            archive.extractall(root, filter="data")
        except TypeError:
            archive.extractall(root)
    print(f"Unpacked AutoGenBook Open WebUI integration bundle ({digest}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
