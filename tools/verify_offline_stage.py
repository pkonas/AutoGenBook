from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any


def find_python(stage: Path) -> Path:
    candidates = [
        stage / "runtime" / "python" / "python.exe",
        stage / "runtime" / "python" / "bin" / "python3",
        stage / "runtime" / "python" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise RuntimeError("Bundled Python executable is missing")


def build_env(stage: Path) -> dict[str, str]:
    env = {str(k): str(v) for k, v in os.environ.items()}
    tools = stage / "tools"
    entries: list[str] = []
    common = tools / "bin"
    if common.is_dir():
        entries.append(str(common))
    tex_bin = tools / "tinytex" / "bin"
    if tex_bin.is_dir():
        entries.extend(str(item) for item in sorted(tex_bin.iterdir()) if item.is_dir())
    python = find_python(stage)
    entries.extend(
        str(item)
        for item in (python.parent, python.parent / "Scripts", python.parent / "bin")
        if item.is_dir()
    )
    current = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([*entries, current] if current else entries)
    pandoc = common / ("pandoc.exe" if os.name == "nt" else "pandoc")
    ffmpeg = common / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    env["PYPANDOC_PANDOC"] = str(pandoc)
    env["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg)
    env["FFMPEG_BINARY"] = str(ffmpeg)
    env["AUTOGENBOOK_PIPER_MODEL"] = str(tools / "piper" / "cs_CZ-jirka-medium.onnx")
    env["AUTOGENBOOK_PIPER_CONFIG"] = str(tools / "piper" / "cs_CZ-jirka-medium.onnx.json")
    native = tools / "lib"
    if native.is_dir() and os.name != "nt":
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = os.pathsep.join([str(native), existing] if existing else [str(native)])
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(command: list[str], *, env: dict[str, str], cwd: Path | None = None) -> dict[str, Any]:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, check=True, capture_output=True, text=True, env=env, cwd=cwd)
    return {
        "command": command,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
        "returncode": result.returncode,
    }


def executable(name: str, env: dict[str, str]) -> str:
    found = shutil.which(name, path=env.get("PATH"))
    if not found:
        raise RuntimeError(f"Required bundled executable is missing from PATH: {name}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify that a staged installer works without network access.")
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stage = args.stage.resolve()
    python = find_python(stage)
    env = build_env(stage)
    checks: list[dict[str, Any]] = []

    checks.append(run([str(python), "-m", "pip", "check"], env=env))
    imports = [
        "fastapi", "uvicorn", "pydantic", "multipart", "keyring", "openai", "networkx",
        "pylatex", "latex2markdown", "rank_bm25", "pypdf", "docx", "pptx", "requests",
        "pypandoc", "matplotlib", "pandas", "sklearn", "numpy", "scipy", "soundfile",
        "sounddevice", "pydub", "fitz", "moviepy", "imageio", "imageio_ffmpeg", "piper",
        "autogenbook_companion",
    ]
    import_code = "\n".join(f"import {name}" for name in imports) + "\nprint('all imports ok')"
    checks.append(run([str(python), "-c", import_code], env=env))

    for binary, args_list in (
        ("pandoc", ["--version"]),
        ("ffmpeg", ["-version"]),
        ("lualatex", ["--version"]),
        ("pdflatex", ["--version"]),
        ("bibtex", ["--version"]),
    ):
        checks.append(run([executable(binary, env), *args_list], env=env))

    with tempfile.TemporaryDirectory(prefix="autogenbook-offline-verify-") as temp_raw:
        temp = Path(temp_raw)
        md = temp / "sample.md"
        md.write_text("# Český offline test\n\nPříliš žluťoučký kůň úpěl ďábelské ódy.\n", encoding="utf-8")
        docx = temp / "sample.docx"
        checks.append(run([executable("pandoc", env), str(md), "-o", str(docx)], env=env))
        if not docx.exists() or docx.stat().st_size < 1000:
            raise RuntimeError("Bundled Pandoc did not create a valid DOCX file")

        tex = temp / "sample.tex"
        tex.write_text(
            "\\documentclass{article}\n"
            "\\usepackage{fontspec}\n"
            "\\usepackage[czech]{babel}\n"
            "\\usepackage{amsmath,amssymb,mathtools,physics}\n"
            "\\usepackage{hyperref,booktabs,listings,xcolor}\n"
            "\\begin{document}\n"
            "Český offline test: Příliš žluťoučký kůň. $E=mc^2$.\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        checks.append(
            run(
                [executable("lualatex", env), "-interaction=nonstopmode", "-halt-on-error", tex.name],
                env=env,
                cwd=temp,
            )
        )
        if not (temp / "sample.pdf").exists():
            raise RuntimeError("Bundled LuaLaTeX did not create a PDF")

        beamer = temp / "slides.tex"
        beamer.write_text(
            "\\documentclass{beamer}\n"
            "\\usepackage[T1]{fontenc}\n"
            "\\usepackage[utf8]{inputenc}\n"
            "\\usepackage[czech]{babel}\n"
            "\\begin{document}\n\\begin{frame}{Test}Offline prezentace\\end{frame}\n\\end{document}\n",
            encoding="utf-8",
        )
        checks.append(
            run(
                [executable("pdflatex", env), "-interaction=nonstopmode", "-halt-on-error", beamer.name],
                env=env,
                cwd=temp,
            )
        )
        if not (temp / "slides.pdf").exists():
            raise RuntimeError("Bundled pdfLaTeX/Beamer did not create a PDF")

        wav = temp / "silence.wav"
        checks.append(
            run(
                [
                    executable("ffmpeg", env), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.2", str(wav),
                ],
                env=env,
            )
        )
        if not wav.exists() or wav.stat().st_size < 1000:
            raise RuntimeError("Bundled FFmpeg did not create an audio file")

        speech = temp / "czech-tts.wav"
        tts_code = (
            "import os,wave\n"
            "from piper import PiperVoice\n"
            "voice=PiperVoice.load(os.environ['AUTOGENBOOK_PIPER_MODEL'], os.environ['AUTOGENBOOK_PIPER_CONFIG'])\n"
            f"out={str(speech)!r}\n"
            "with wave.open(out,'wb') as f: voice.synthesize_wav('Dobrý den. Toto je offline test.', f)\n"
            "print(out)\n"
        )
        checks.append(run([str(python), "-c", tts_code], env=env))
        with wave.open(str(speech), "rb") as stream:
            if stream.getnframes() <= 0:
                raise RuntimeError("Bundled Piper voice produced an empty WAV")

    doctor_home = stage / ".verification-home"
    if doctor_home.exists():
        shutil.rmtree(doctor_home)
    checks.append(
        run(
            [
                str(python), "-m", "autogenbook_companion.cli", "doctor",
                "--home", str(doctor_home), "--source-dir", str(stage / "app"),
            ],
            env=env,
        )
    )
    shutil.rmtree(doctor_home, ignore_errors=True)

    report = {
        "ok": True,
        "stage": str(stage),
        "python": str(python),
        "network_used_during_verification": False,
        "checks": checks,
    }
    output = args.output.resolve() if args.output else stage / "OFFLINE-VERIFICATION.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
