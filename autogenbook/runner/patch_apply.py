from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List


FORBIDDEN_IMPORTS = ("requests", "urllib", "socket", "subprocess", "os.system")
SUSPICIOUS_STRINGS = ("/etc/", "rm -rf", "curl", "wget")


@dataclass
class PatchApplyResult:
    changed_files: List[Path]
    backup_dir: Path


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[str]


@dataclass
class FilePatch:
    path: Path
    hunks: List[Hunk]


def apply_unified_diff(diff_text: str, workdir: Path) -> PatchApplyResult:
    diff_text = diff_text or ""
    if not diff_text.strip():
        return PatchApplyResult(changed_files=[], backup_dir=Path())

    _safety_scan_diff(diff_text)
    patches = _parse_unified_diff(diff_text, workdir)

    backup_dir = workdir / ".patch_backups" / str(int(time.time()))
    backup_dir.mkdir(parents=True, exist_ok=True)

    changed_files: List[Path] = []
    for file_patch in patches:
        target_path = workdir / file_patch.path
        if not target_path.exists():
            raise RuntimeError(f"Patch target does not exist: {file_patch.path}")

        backup_path = backup_dir / file_patch.path
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(target_path.read_bytes())

        lines = target_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        for hunk in file_patch.hunks:
            lines = _apply_hunk(lines, hunk)

        target_path.write_text("".join(lines), encoding="utf-8")
        changed_files.append(target_path)

    return PatchApplyResult(changed_files=changed_files, backup_dir=backup_dir)


def restore_backups(backup_dir: Path, workdir: Path) -> None:
    if not backup_dir or not backup_dir.exists():
        return
    for backup in backup_dir.rglob("*"):
        if not backup.is_file():
            continue
        rel = backup.relative_to(backup_dir)
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(backup.read_bytes())


def _safety_scan_diff(diff_text: str) -> None:
    lowered = diff_text.lower()
    for token in SUSPICIOUS_STRINGS:
        if token in lowered:
            raise RuntimeError(f"Patch rejected: suspicious string '{token}' detected.")

    for line in diff_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        lowered_line = line.lower()
        for forbidden in FORBIDDEN_IMPORTS:
            if forbidden in lowered_line:
                raise RuntimeError(f"Patch rejected: forbidden import '{forbidden}' detected.")


def _parse_unified_diff(diff_text: str, workdir: Path) -> List[FilePatch]:
    lines = diff_text.splitlines()
    idx = 0
    patches: List[FilePatch] = []

    while idx < len(lines):
        line = lines[idx]
        if line.startswith("--- "):
            old_path = line[4:].strip()
            idx += 1
            if idx >= len(lines) or not lines[idx].startswith("+++ "):
                raise RuntimeError("Invalid unified diff: missing '+++' line.")
            new_path = lines[idx][4:].strip()
            idx += 1
            path = _sanitize_patch_path(new_path, workdir)
            hunks: List[Hunk] = []
            while idx < len(lines) and lines[idx].startswith("@@ "):
                header = lines[idx]
                idx += 1
                hunk_lines: List[str] = []
                while idx < len(lines) and not lines[idx].startswith("@@ ") and not lines[idx].startswith("--- "):
                    hunk_lines.append(lines[idx])
                    idx += 1
                hunk = _parse_hunk_header(header, hunk_lines)
                hunks.append(hunk)
            patches.append(FilePatch(path=path, hunks=hunks))
            continue
        idx += 1

    if not patches:
        raise RuntimeError("No file patches found in diff.")
    return patches


def _sanitize_patch_path(path_text: str, workdir: Path) -> Path:
    raw = path_text.replace("\\", "/")
    if raw.startswith("a/") or raw.startswith("b/"):
        raw = raw[2:]
    if raw.startswith("/") or raw.startswith("~"):
        raise RuntimeError(f"Absolute paths are not allowed in patches: {path_text}")
    if ".." in raw.split("/"):
        raise RuntimeError(f"Path traversal is not allowed in patches: {path_text}")
    path = Path(raw)
    resolved = (workdir / path).resolve()
    if not str(resolved).startswith(str(workdir.resolve())):
        raise RuntimeError(f"Patch path escapes workdir: {path_text}")
    return path


def _parse_hunk_header(header: str, hunk_lines: List[str]) -> Hunk:
    match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
    if not match:
        raise RuntimeError(f"Invalid hunk header: {header}")
    old_start = int(match.group(1))
    old_count = int(match.group(2) or 1)
    new_start = int(match.group(3))
    new_count = int(match.group(4) or 1)
    return Hunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        lines=hunk_lines,
    )


def _apply_hunk(lines: List[str], hunk: Hunk) -> List[str]:
    start_idx = max(0, hunk.old_start - 1)
    start_idx = _find_hunk_start(lines, hunk, start_idx)
    result: List[str] = []
    result.extend(lines[:start_idx])

    idx = start_idx
    for raw in hunk.lines:
        if not raw:
            continue
        prefix = raw[0]
        content = raw[1:]
        if prefix == " ":
            if idx >= len(lines) or lines[idx].rstrip("\n") != content:
                raise RuntimeError("Hunk context mismatch.")
            result.append(lines[idx])
            idx += 1
        elif prefix == "-":
            if idx >= len(lines) or lines[idx].rstrip("\n") != content:
                raise RuntimeError("Hunk removal mismatch.")
            idx += 1
        elif prefix == "+":
            result.append(content + "\n")
        elif prefix == "\\":
            continue
        else:
            result.append(raw + "\n")

    result.extend(lines[idx:])
    return result


def _find_hunk_start(lines: List[str], hunk: Hunk, start_idx: int) -> int:
    anchor = None
    for raw in hunk.lines:
        if raw.startswith(" ") or raw.startswith("-"):
            anchor = raw[1:]
            break
    if anchor is None:
        return start_idx
    if start_idx < len(lines) and lines[start_idx].rstrip("\n") == anchor:
        return start_idx
    for idx, line in enumerate(lines):
        if line.rstrip("\n") == anchor:
            return idx
    return start_idx
