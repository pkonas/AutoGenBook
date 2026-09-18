#!/usr/bin/env python3
"""Patch the tar.gz AutoGenBook Open WebUI bundle for durable native files."""

from __future__ import annotations

import argparse
import ast
import base64
import gzip
import hashlib
import io
import json
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Iterable

VERSION = "0.3.1"
PART_CHARS = 9000
PART_GLOB = "openwebui_bundle.part*.b64"

NEW_FILE_PAYLOAD = '''    @staticmethod
    def _file_payload(item: Any, persistent_url: str) -> dict[str, Any]:
        """Build the canonical Open WebUI assistant file object.

        ResponseMessage renders only objects whose ``type`` is ``file`` or
        ``image``. FileItemModal then uses the persistent Open WebUI ``id`` to
        call the authenticated file-content endpoint.
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
        try:
            size = max(0, int(meta.get("size", raw.get("size", 0)) or 0))
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_extract_tar(payload: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if target != base and base not in target.parents:
                raise RuntimeError(f"Unsafe integration member: {member.name}")
            if member.issym() or member.islnk():
                raise RuntimeError(f"Links are forbidden in the integration bundle: {member.name}")
        try:
            archive.extractall(destination, filter="data")
        except TypeError:
            archive.extractall(destination)


def deterministic_tar_gz(source: Path) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(source.rglob("*"), key=lambda value: value.as_posix()):
                if not path.is_file():
                    continue
                relative = path.relative_to(source).as_posix()
                info = tarfile.TarInfo(relative)
                data = path.read_bytes()
                info.size = len(data)
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def patch_pipe(path: Path) -> bool:
    original = path.read_text(encoding="utf-8-sig")
    if "def _file_payload(" not in original or "PERSIST_OUTPUTS_TO_OPENWEBUI" not in original:
        return False
    block = re.compile(
        r"(?ms)^    @staticmethod\n"
        r"    def _file_payload\(item: Any, persistent_url: str\) -> dict\[str, Any\]:\n"
        r".*?"
        r"(?=^    async def _publish_artifact_to_openwebui\()"
    )
    if block.search(original):
        modified = block.sub(NEW_FILE_PAYLOAD, original, count=1)
    elif '"type": "file"' in original:
        modified = original
    else:
        raise RuntimeError(f"Could not replace _file_payload in {path}")

    modified = re.sub(r"(?m)^(\s*version:\s*)[0-9]+\.[0-9]+\.[0-9]+\s*$", rf"\g<1>{VERSION}", modified)
    modified = modified.replace(
        'await event_emitter({"type": "files", "data": {"files": registered}})',
        'await event_emitter({"type": "chat:message:files", "data": {"files": registered}})',
    )
    if '"type": "file"' not in modified or "chat:message:files" not in modified:
        raise AssertionError(f"Canonical output contract missing in {path}")
    if modified != original:
        path.write_text(modified, encoding="utf-8", newline="\n")
    return True


def find_pipes(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in {".git", ".venv", "venv", "site-packages", "__pycache__"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if "def _file_payload(" in text and "PERSIST_OUTPUTS_TO_OPENWEBUI" in text:
            result.append(path)
    return sorted(result)


def execute_payload_contract(pipe: Path) -> dict[str, Any]:
    tree = ast.parse(pipe.read_text(encoding="utf-8"), filename=str(pipe))
    method: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Pipe":
            method = next(
                (
                    child
                    for child in node.body
                    if isinstance(child, ast.FunctionDef) and child.name == "_file_payload"
                ),
                None,
            )
    if method is None:
        raise AssertionError("Pipe._file_payload was not found")
    probe_class = ast.ClassDef(
        name="ProbePipe",
        bases=[],
        keywords=[],
        body=[method],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(
        ast.Module(
            body=[ast.ImportFrom(module="typing", names=[ast.alias(name="Any")], level=0), probe_class],
            type_ignores=[],
        )
    )
    namespace: dict[str, Any] = {"CompanionError": RuntimeError}
    exec(compile(module, str(pipe), "exec"), namespace)

    class Item:
        id = "123e4567-e89b-12d3-a456-426614174000"
        filename = "review_final.pdf"

        def model_dump(self, **_: Any) -> dict[str, Any]:
            return {
                "id": self.id,
                "filename": self.filename,
                "meta": {
                    "name": self.filename,
                    "size": 12345,
                    "content_type": "application/pdf",
                },
            }

    url = f"/api/v1/files/{Item.id}/content?attachment=true"
    payload = namespace["ProbePipe"]._file_payload(Item(), url)
    expected = {
        "type": "file",
        "id": Item.id,
        "name": Item.filename,
        "size": 12345,
        "content_type": "application/pdf",
        "status": "uploaded",
        "url": url,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise AssertionError(f"Unexpected payload {key}: {payload.get(key)!r}")
    if "token=" in payload["url"] or "127.0.0.1" in payload["url"]:
        raise AssertionError("A temporary Companion URL escaped into the user-facing payload")
    return payload


def update_unpack_helper(project: Path, digest: str) -> None:
    helper = project / "tools" / "unpack_openwebui_bundle.py"
    if not helper.is_file():
        return
    original = helper.read_text(encoding="utf-8-sig")
    modified = re.sub(
        r'EXPECTED_SHA256\s*=\s*"[0-9a-f]{64}"',
        f'EXPECTED_SHA256 = "{digest}"',
        original,
        count=1,
    )
    if modified == original:
        raise RuntimeError("Could not update the integration bundle expected hash")
    helper.write_text(modified, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    project = args.project_root.resolve()
    integration = project / "integration"
    parts = sorted(integration.glob(PART_GLOB))
    if not parts:
        raise FileNotFoundError(f"No {PART_GLOB} files found")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in parts)
    original_payload = base64.b64decode(encoded, validate=True)

    with tempfile.TemporaryDirectory(prefix="agb-v031-") as temporary:
        workspace = Path(temporary)
        bundle = workspace / "bundle"
        safe_extract_tar(original_payload, bundle)
        patched: list[str] = []
        contracts: list[dict[str, Any]] = []
        roots = [bundle]
        overlay = project / "tools" / "openwebui_overlays"
        if overlay.is_dir():
            roots.append(overlay)
        for root in roots:
            for pipe in find_pipes(root):
                if patch_pipe(pipe):
                    patched.append(str(pipe.relative_to(root)))
                    contracts.append(execute_payload_contract(pipe))
        if not patched:
            raise RuntimeError("No Open WebUI Pipe with persistent-output support was found")

        for root in (project, bundle):
            for name in ("VERSION", "version.txt"):
                marker = root / name
                if marker.is_file():
                    marker.write_text(VERSION + "\n", encoding="utf-8")

        rebuilt = deterministic_tar_gz(bundle)
        rebuilt_digest = bytes_sha256(rebuilt)
        for part in parts:
            part.unlink(missing_ok=True)
        rebuilt_encoded = base64.b64encode(rebuilt).decode("ascii")
        created = []
        for index, offset in enumerate(range(0, len(rebuilt_encoded), PART_CHARS), start=1):
            path = integration / f"openwebui_bundle.part{index:03d}.b64"
            path.write_text(rebuilt_encoded[offset : offset + PART_CHARS] + "\n", encoding="ascii")
            created.append(path.name)
        (integration / "openwebui_bundle.sha256").write_text(rebuilt_digest + "\n", encoding="ascii")
        update_unpack_helper(project, rebuilt_digest)

        report = {
            "version": VERSION,
            "format": "tar.gz+base64-parts",
            "source_bundle_sha256": bytes_sha256(original_payload),
            "patched_files": patched,
            "payload_contracts": contracts,
            "bundle": {
                "sha256": rebuilt_digest,
                "size": len(rebuilt),
                "parts": created,
            },
        }
        report_path = args.report or project / "build" / "durable-output-patch-report-v0.3.1.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
