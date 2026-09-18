#!/usr/bin/env python3
"""Upgrade an AutoGenBook Open WebUI Function 0.3.0 to 0.3.1.

The updater is intentionally self-contained. It never touches AutoGenBook job
state, projects, generated artifacts or API credentials. It patches only the
Open WebUI Pipe source, writes a backup, emits a standalone importable Function
and validates the real payload adapter before reporting success.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

VERSION = "0.3.1"

NEW_METHOD = '''    @staticmethod
    def _file_payload(item: Any, persistent_url: str) -> dict[str, Any]:
        """Return a native, persistent Open WebUI assistant-file object.

        Open WebUI renders response attachments only when their ``type`` is
        ``file`` or ``image``.  The persistent Open WebUI file id and
        same-origin content URL remain valid independently of the Companion
        token, random port or process lifetime.
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


def is_candidate(path: Path) -> bool:
    if not path.is_file() or path.suffix.casefold() != ".py":
        return False
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return False
    return (
        "id: autogenbook_companion" in text
        and "class Pipe:" in text
        and "def _file_payload(" in text
        and "PERSIST_OUTPUTS_TO_OPENWEBUI" in text
    )


def default_roots() -> list[Path]:
    roots = [Path.cwd()]
    home = Path.home()
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        roots.extend(
            [
                local / "Programs" / "AutoGenBook OpenWebUI",
                local / "AutoGenBook-OpenWebUI",
            ]
        )
    elif sys.platform == "darwin":
        roots.append(home / "Library" / "Application Support" / "AutoGenBook-OpenWebUI")
    else:
        roots.append(Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "autogenbook-openwebui")
    result: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            key = str(root.expanduser().resolve())
        except OSError:
            key = str(root.expanduser())
        if key not in seen:
            seen.add(key)
            result.append(Path(key))
    return result


def discover(explicit: list[Path]) -> list[Path]:
    candidates: list[Path] = []
    roots = explicit or default_roots()
    for root in roots:
        root = root.expanduser()
        if root.is_file():
            if is_candidate(root):
                candidates.append(root.resolve())
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if any(part in {".git", ".venv", "venv", "site-packages", "__pycache__"} for part in path.parts):
                continue
            if is_candidate(path):
                candidates.append(path.resolve())
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return sorted(unique)


def patch_text(source: str) -> str:
    pattern = re.compile(
        r"(?ms)^    @staticmethod\n"
        r"    def _file_payload\(item: Any, persistent_url: str\) -> dict\[str, Any\]:\n"
        r".*?"
        r"(?=^    async def _stream_companion_artifact\()"
    )
    if pattern.search(source):
        result = pattern.sub(NEW_METHOD, source, count=1)
    elif '"type": "file"' in source and '"status": "uploaded"' in source:
        result = source
    else:
        raise RuntimeError("The expected AutoGenBook 0.3.0 _file_payload block was not found.")

    result = re.sub(
        r"(?m)^(\s*version:\s*)0\.3\.0\s*$",
        rf"\g<1>{VERSION}",
        result,
        count=1,
    )
    # Both event names are accepted by current Open WebUI; use the namespaced
    # form so the intent is explicit while retaining the same payload schema.
    result = result.replace(
        'await event_emitter({"type": "files", "data": {"files": registered}})',
        'await event_emitter({"type": "chat:message:files", "data": {"files": registered}})',
    )
    return result


def method_contract(source: str, filename: str) -> dict[str, Any]:
    tree = ast.parse(source, filename=filename)
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
        raise AssertionError("Pipe._file_payload is missing after patching")
    probe = ast.fix_missing_locations(
        ast.Module(
            body=[
                ast.ImportFrom(module="typing", names=[ast.alias(name="Any")], level=0),
                ast.ClassDef(
                    name="ProbePipe",
                    bases=[],
                    keywords=[],
                    body=[method],
                    decorator_list=[],
                ),
            ],
            type_ignores=[],
        )
    )
    namespace: dict[str, Any] = {"CompanionError": RuntimeError}
    exec(compile(probe, filename, "exec"), namespace)

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
            raise AssertionError(f"Invalid file payload field {key}: {payload.get(key)!r}")
    if "token=" in payload["url"] or "127.0.0.1" in payload["url"]:
        raise AssertionError("A temporary Companion URL was emitted")
    return payload


def write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".v031.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def patch_file(path: Path, output_dir: Path, in_place: bool) -> dict[str, Any]:
    original = path.read_text(encoding="utf-8-sig")
    patched = patch_text(original)
    payload = method_contract(patched, str(path))
    output_dir.mkdir(parents=True, exist_ok=True)
    standalone = output_dir / "AutoGenBook-OpenWebUI-Function-v0.3.1.py"
    write_atomic(standalone, patched)
    backup = None
    if in_place:
        backup = path.with_suffix(path.suffix + ".v0.3.0.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
        write_atomic(path, patched)
    return {
        "source": str(path),
        "standalone_function": str(standalone),
        "in_place": in_place,
        "backup": str(backup) if backup else None,
        "payload_contract": payload,
    }


def self_test() -> dict[str, Any]:
    sample = '''"""\ntitle: AutoGenBook Companion\nid: autogenbook_companion\nversion: 0.3.0\n"""\nfrom typing import Any\nclass CompanionError(RuntimeError):\n    pass\nclass Pipe:\n    PERSIST_OUTPUTS_TO_OPENWEBUI = True\n    @staticmethod\n    def _file_payload(item: Any, persistent_url: str) -> dict[str, Any]:\n        payload = item.model_dump(exclude={"path"}) if hasattr(item, "model_dump") else dict(item)\n        payload["url"] = persistent_url\n        payload.setdefault("name", payload.get("filename"))\n        payload["source"] = "autogenbook"\n        return payload\n    async def _stream_companion_artifact(self):\n        pass\n    async def _emit(self, event_emitter, registered):\n        await event_emitter({"type": "files", "data": {"files": registered}})\n'''
    patched = patch_text(sample)
    if "version: 0.3.1" not in patched or "chat:message:files" not in patched:
        raise AssertionError("Version/event patch did not apply")
    return method_contract(patched, "<self-test>")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function-file", type=Path, action="append", default=[])
    parser.add_argument("--search-root", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if args.self_test:
        report = {"version": VERSION, "self_test": self_test()}
        report_path = args.report or Path("build/durable-downloads-v0.3.1-self-test.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    explicit = [*args.function_file, *args.search_root]
    candidates = discover(explicit)
    if not candidates:
        print(
            "No AutoGenBook 0.3.0 Function source was found. Pass its path with "
            "--function-file, for example the file exported from Open WebUI Admin Panel.",
            file=sys.stderr,
        )
        return 2
    results = [
        patch_file(path, args.output_dir.resolve(), bool(args.in_place))
        for path in candidates
    ]
    report = {"version": VERSION, "patched": results}
    report_path = args.report or args.output_dir / "durable-downloads-v0.3.1-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
