from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Split a large release file into independently downloadable chunks.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chunk-size-mib", type=int, default=300)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    source = args.input.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    chunk_bytes = args.chunk_size_mib * 1024 * 1024
    chunks: list[dict[str, object]] = []
    with source.open("rb") as stream:
        index = 1
        while True:
            data = stream.read(chunk_bytes)
            if not data:
                break
            path = output / f"{args.label}.part{index:03d}"
            path.write_bytes(data)
            chunks.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
            index += 1
    manifest = {
        "label": args.label,
        "original_name": source.name,
        "original_size": source.stat().st_size,
        "original_sha256": sha256_file(source),
        "chunk_size_mib": args.chunk_size_mib,
        "chunks": chunks,
    }
    manifest_path = output / f"{args.label}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output / f"join-{args.label}.sh").write_text(
        "#!/usr/bin/env sh\nset -eu\ncat "
        + " ".join(str(item["name"]) for item in chunks)
        + f" > {source.name}\nprintf '%s  %s\\n' '{manifest['original_sha256']}' '{source.name}'\n",
        encoding="utf-8",
    )
    (output / f"join-{args.label}.cmd").write_text(
        "@echo off\r\ncopy /b "
        + "+".join(str(item["name"]) for item in chunks)
        + f" {source.name} >nul\r\necho SHA256 expected: {manifest['original_sha256']}\r\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
