#!/usr/bin/env python3
"""Patch AutoGenBook Open WebUI integration for durable, downloadable output files.

The v0.3.0 Pipe registered artifact bytes in Open WebUI but emitted a payload
without ``type: file``.  Current Open WebUI renders assistant attachments only
when the event objects have ``type`` equal to ``file`` or ``image``.  This
patcher replaces the payload adapter with the canonical shape and validates
that local-provider bytes exist before announcing a successful publication.

It can patch a checked-out integration source tree or an unpacked integration
bundle.  It is intentionally deterministic and safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

TARGET_VERSION = "0.3.1"
BUNDLE_GLOB = "openwebui_bundle.part*.b64"
PART_CHARS = 9000

NEW_FILE_PAYLOAD = '''    @staticmethod
    def _file_payload(item: Any, persistent_url: str) -> dict[str, Any]:
        """Return the canonical assistant-file object expected by Open WebUI.

        Open WebUI's response renderer ignores entries without ``type=file``.
        The modal and download code then use ``id`` to call the authenticated
        ``/api/v1/files/<id>/content`` endpoint.  Keep the complete FileModel
        data for forward compatibility, but normalize the fields consumed by
        the current UI explicitly.
        """
        raw = (
            item.model_dump(exclude={"path"})
            if hasattr(item, "model_dump")
            else dict(item)
        )
        file_id = str(raw.get("id") or getattr(item, "id", "") or "").strip()
        if not file_id:
            raise CompanionError("Open WebUI returned a file without an id.")

        raw_meta = raw.get("meta")
        meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        name = str(
            meta.get("name")
            or raw.get("name")
            or raw.get("filename")
            or getattr(item, "filename", "")
            or f"{file_id}.bin"
        )
        size_raw = meta.get("size", raw.get("size", 0))
        try:
            size = max(0, int(size_raw or 0))
        except (TypeError, ValueError):
            size = 0
        content_type = str(
            meta.get("content_type")
            or raw.get("content_type")
            or "application/octet-stream"
        )
        meta.update(
            {
                "name": name,
                "size": size,
                "content_type": content_type,
                "source": "autogenbook",
            }
        )

        raw.update(
            {
                "type": "file",
                "id": file_id,
                "url": persistent_url,
                "name": name,
                "filename": str(raw.get("filename") or name),
                "size": size,
                "content_type": content_type,
                "status": "uploaded",
                "source": "autogenbook",
                "meta": meta,
            }
        )
        return raw

'''


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_zip(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*"), key=lambda value: value.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes())


def replace_version(text: str) -> str:
    text = re.sub(r'(?m)^(\s*version:\s*)0\.3\.0\s*$', rf'\g<1>{TARGET_VERSION}', text)
    text = re.sub(r'(?m)^(__version__\s*=\s*["\'])0\.3\.0(["\'])$', rf'\g<1>{TARGET_VERSION}\g<2>', text)
    return text


def patch_pipe(path: Path) -> bool:
    original = path.read_text(encoding="utf-8-sig")
    if "def _file_payload(" not in original or "PERSIST_OUTPUTS_TO_OPENWEBUI" not in original:
        return False

    # Replace the entire adapter method, preserving the following method.
    pattern = re.compile(
        r"(?ms)^    @staticmethod\n"
        r"    def _file_payload\(item: Any, persistent_url: str\) -> dict\[str, Any\]:\n"
        r".*?"
        r"(?=^    async def _publish_artifact_to_openwebui\()"
    )
    if pattern.search(original):
        modified = pattern.sub(NEW_FILE_PAYLOAD, original, count=1)
    elif 'payload["type"] = "file"' in original or '"type": "file"' in original:
        modified = original
    else:
        raise RuntimeError(f"Could not locate _file_payload block in {path}")

    modified = replace_version(modified)
    if '"type": "file"' not in modified:
        raise RuntimeError(f"Canonical type=file field was not installed in {path}")

    # A stable relative URL is correct; the UI resolves it against its own
    # authenticated origin and the FileItemModal downloads by file id.
    modified = modified.replace(
        'await event_emitter({"type": "files", "data": {"files": registered}})',
        'await event_emitter({"type": "chat:message:files", "data": {"files": registered}})',
    )

    if modified != original:
        path.write_text(modified, encoding="utf-8", newline="\n")
    return True


def discover_pipe_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in {".git", ".venv", "venv", "site-packages", "__pycache__"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if "def _file_payload(" in text and "PERSIST_OUTPUTS_TO_OPENWEBUI" in text:
            candidates.append(path)
    return sorted(candidates)


def test_payload_contract(pipe_path: Path) -> dict[str, object]:
    source = pipe_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(pipe_path))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Pipe":
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "_file_payload":
                    method = child
                    break
    if method is None or not isinstance(method, ast.FunctionDef):
        raise AssertionError("Pipe._file_payload was not found")

    # Compile the real method body in a small synthetic class.
    class_node = ast.ClassDef(
        name="ProbePipe",
        bases=[],
        keywords=[],
        body=[method],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(
        ast.Module(
            body=[
                ast.ImportFrom(module="typing", names=[ast.alias(name="Any")], level=0),
                class_node,
            ],
            type_ignores=[],
        )
    )
    namespace: dict[str, object] = {"CompanionError": RuntimeError}
    exec(compile(module, str(pipe_path), "exec"), namespace)

    class Item:
        id = "123e4567-e89b-12d3-a456-426614174000"
        filename = "review_final.pdf"

        def model_dump(self, **_: object) -> dict[str, object]:
            return {
                "id": self.id,
                "filename": self.filename,
                "meta": {
                    "name": self.filename,
                    "size": 12345,
                    "content_type": "application/pdf",
                },
            }

    payload = namespace["ProbePipe"]._file_payload(
        Item(),
        "/api/v1/files/123e4567-e89b-12d3-a456-426614174000/content?attachment=true",
    )
    expected = {
        "type": "file",
        "id": Item.id,
        "name": Item.filename,
        "size": 12345,
        "content_type": "application/pdf",
        "status": "uploaded",
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise AssertionError(f"payload[{key!r}]={payload.get(key)!r}, expected {value!r}")
    if "127.0.0.1" in str(payload.get("url")) or "token=" in str(payload.get("url")):
        raise AssertionError("User-facing payload still contains a Companion address or token")
    return payload


def unpack_bundle(project_root: Path, work_root: Path) -> tuple[Path, list[Path]]:
    integration = project_root / "integration"
    parts = sorted(integration.glob(BUNDLE_GLOB))
    if not parts:
        raise FileNotFoundError(f"No {BUNDLE_GLOB} files under {integration}")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in parts)
    archive_bytes = base64.b64decode(encoded, validate=True)
    archive_path = work_root / "openwebui_bundle.zip"
    archive_path.write_bytes(archive_bytes)
    bundle_root = work_root / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            target = (bundle_root / info.filename).resolve()
            if bundle_root.resolve() not in target.parents and target != bundle_root.resolve():
                raise RuntimeError(f"Unsafe bundle path: {info.filename}")
        archive.extractall(bundle_root)
    return bundle_root, parts


def repack_bundle(project_root: Path, bundle_root: Path, old_parts: Iterable[Path], work_root: Path) -> dict[str, object]:
    archive_path = work_root / "openwebui_bundle.patched.zip"
    deterministic_zip(bundle_root, archive_path)
    encoded = base64.b64encode(archive_path.read_bytes()).decode("ascii")
    integration = project_root / "integration"
    for part in old_parts:
        part.unlink(missing_ok=True)
    created: list[str] = []
    for index, offset in enumerate(range(0, len(encoded), PART_CHARS), start=1):
        path = integration / f"openwebui_bundle.part{index:03d}.b64"
        path.write_text(encoded[offset : offset + PART_CHARS] + "\n", encoding="ascii")
        created.append(path.name)
    (integration / "openwebui_bundle.sha256").write_text(sha256(archive_path) + "\n", encoding="ascii")
    return {
        "archive": str(archive_path),
        "sha256": sha256(archive_path),
        "size": archive_path.stat().st_size,
        "parts": created,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    project_root = args.project_root.resolve()

    with tempfile.TemporaryDirectory(prefix="autogenbook-v031-") as temporary:
        work_root = Path(temporary)
        bundle_root, old_parts = unpack_bundle(project_root, work_root)
        patched: list[str] = []
        payload_samples: list[dict[str, object]] = []

        roots = [bundle_root]
        overlay = project_root / "tools" / "openwebui_overlays"
        if overlay.exists():
            roots.append(overlay)
        for root in roots:
            for path in discover_pipe_files(root):
                if patch_pipe(path):
                    patched.append(str(path.relative_to(root)))
                    payload_samples.append(test_payload_contract(path))

        if not patched:
            raise RuntimeError("No AutoGenBook Open WebUI Pipe was patched")

        # Keep version markers aligned when they exist.
        for root in (project_root, bundle_root):
            for relative in ("VERSION", "version.txt"):
                marker = root / relative
                if marker.is_file():
                    marker.write_text(TARGET_VERSION + "\n", encoding="utf-8")

        bundle = repack_bundle(project_root, bundle_root, old_parts, work_root)
        report = {
            "version": TARGET_VERSION,
            "patched_files": patched,
            "payload_contracts": payload_samples,
            "bundle": bundle,
        }
        report_path = args.report or (project_root / "build" / "durable-output-patch-report-v0.3.1.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
