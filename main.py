from __future__ import annotations

import argparse
import os
from pathlib import Path

from autogenbook.orchestrator import run as run_orchestrator
from autogenbook.state import RunContext

os.environ["OPENROUTER_INPUT_COST_PER_M"] = "0.25"
os.environ["OPENROUTER_OUTPUT_COST_PER_M"] = "2"
os.environ["PYTHONUTF8"] = "1"


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="autogenbook",
        description="AutoGenBook CLI (OpenRouter + optional RAG knowledge base).",
    )
    p.add_argument(
        "--mode",
        choices=["book", "paper", "presentation", "scientist", "proposal", "reviewer"],
        default="book",
        help="Režim běhu (default: book).",
    )
    p.add_argument(
        "--paper-venue",
        default="arXiv",
        help="Cílové venue pro paper mode (default: arXiv).",
    )
    p.add_argument(
        "--citation-style",
        choices=["bibtex", "footnote"],
        default="bibtex",
        help="Styl citací pro paper mode (default: bibtex).",
    )
    p.add_argument(
        "--enable-web-rag",
        action="store_true",
        help="Povolit webovAc RAG (MCP paper tools/Tavily) pro book/paper/presentation/scientist mA^d.",
    )
    p.add_argument(
        "--web-rag-k",
        default=5,
        type=int,
        help="Počet webových výsledků pro RAG (default: 5).",
    )
    p.add_argument(
        "--audit",
        action="store_true",
        default=None,
        help="Zapnout audit LaTeX vystupu (default: on for paper).",
    )
    p.add_argument(
        "--audit-mode",
        choices=["off", "warn", "strict"],
        default="warn",
        help="Rezim auditu: off|warn|strict (default: warn).",
    )
    p.add_argument(
        "--audit-window-chars",
        type=int,
        default=600,
        help="Velikost okna pro hledani evidence u cisel (default: 600).",
    )
    p.add_argument(
        "--audit-book",
        action="store_true",
        help="Zapnout audit v book mode (default: off).",
    )
    p.add_argument(
        "--audit-book-mode",
        choices=["off", "warn", "strict"],
        default="warn",
        help="Rezim auditu pro book: off|warn|strict (default: warn).",
    )
    p.add_argument(
        "--export-tex",
        action="store_true",
        help="Book mode: export TeX/PDF from Markdown output (requires pandoc).",
    )
    p.add_argument(
        "--legacy-tex",
        action="store_true",
        help="Book/Paper mode: use legacy LLM LaTeX generation instead of Markdown-first.",
    )
    p.add_argument(
        "--fail-fast-schema",
        action="store_true",
        help="Pri chybe validace schematu ukoncit beh bez dodatecnych napoved.",
    )
    p.add_argument(
        "-i",
        "--input",
        default="book_input.txt",
        help="Vstupni TXT soubor se specifikaci knihy (default: book_input.txt).",
    )
    p.add_argument(
        "--presentation-input",
        default="presentation_input.txt",
        help="Vstupni TXT soubor pro presentation mode (default: presentation_input.txt).",
    )
    p.add_argument(
        "--proposal-input",
        default="proposal_input.txt",
        help="Vstupni TXT soubor pro proposal mode (default: proposal_input.txt).",
    )
    p.add_argument(
        "--max-iters",
        type=int,
        default=None,
        help="Maximalni pocet iteraci (proposal default: 3).",
    )
    p.add_argument(
        "--section-retries",
        type=int,
        default=3,
        help="Maximalni pocet pokusu na sekci pro proposal mode (default: 3).",
    )
    p.add_argument(
        "--min-section-citations",
        type=int,
        default=1,
        help="Minimalni pocet citaci na sekci v proposal mode (default: 1).",
    )
    p.add_argument(
        "--dont-ask",
        "--dont_ask",
        action="store_true",
        help="Vypnout dotazy na uzivatele v proposal mode; chybejici informace se budou hledat v KB a webu.",
    )
    p.add_argument(
        "--proposal-llm1-model",
        default=None,
        help="Model override pro proposal LLM1 (architect).",
    )
    p.add_argument(
        "--proposal-llm2-model",
        default=None,
        help="Model override pro proposal LLM2 (researcher outline).",
    )
    p.add_argument(
        "--proposal-llm3-model",
        default=None,
        help="Model override pro proposal LLM3 (opponent outline).",
    )
    p.add_argument(
        "--proposal-llm4-model",
        default=None,
        help="Model override pro proposal LLM4 (researcher writer).",
    )
    p.add_argument(
        "--proposal-llm5-model",
        default=None,
        help="Model override pro proposal LLM5 (opponent final).",
    )
    p.add_argument(
        "--proposal-llm1-base-url",
        default=None,
        help="Base URL override pro proposal LLM1 (architect).",
    )
    p.add_argument(
        "--proposal-llm2-base-url",
        default=None,
        help="Base URL override pro proposal LLM2 (researcher outline).",
    )
    p.add_argument(
        "--proposal-llm3-base-url",
        default=None,
        help="Base URL override pro proposal LLM3 (opponent outline).",
    )
    p.add_argument(
        "--proposal-llm4-base-url",
        default=None,
        help="Base URL override pro proposal LLM4 (researcher writer).",
    )
    p.add_argument(
        "--proposal-llm5-base-url",
        default=None,
        help="Base URL override pro proposal LLM5 (opponent final).",
    )
    p.add_argument(
        "--reviewer-llm1-model",
        default=None,
        help="Model override pro reviewer LLM1 (architect).",
    )
    p.add_argument(
        "--reviewer-llm2-model",
        default=None,
        help="Model override pro reviewer LLM2 (reviewer).",
    )
    p.add_argument(
        "--reviewer-llm1-base-url",
        default=None,
        help="Base URL override pro reviewer LLM1 (architect).",
    )
    p.add_argument(
        "--reviewer-llm2-base-url",
        default=None,
        help="Base URL override pro reviewer LLM2 (reviewer).",
    )
    p.add_argument(
        "--llm-base-url",
        default=None,
        help="Override OpenAI-compatible LLM base URL (e.g. http://localhost:1234/v1).",
    )
    p.add_argument(
        "--reviewer-direct-pdf",
        action="store_true",
        help="Reviewer mode: include direct PDF text in LLM2 prompt when available.",
    )
    p.add_argument(
        "--presentation-tex",
        action="store_true",
        help="Presentation mode: convert Markdown to Beamer LaTeX/PDF.",
    )
    p.add_argument(
        "--presentation-pptx",
        action="store_true",
        help="Presentation mode: export Markdown deck to PowerPoint PPTX.",
    )
    p.add_argument(
        "--presentation-narration",
        action="store_true",
        help="Presentation mode: generate spoken narration text per slide.",
    )
    p.add_argument(
        "--presentation-narration-model",
        default=None,
        help="Model override for presentation narration.",
    )
    p.add_argument(
        "--presentation-tts",
        action="store_true",
        help="Presentation mode: synthesize audio from narration.",
    )
    p.add_argument(
        "--presentation-tts-mode",
        choices=["local", "openrouter"],
        default="openrouter",
        help="TTS backend for presentation mode (default: openrouter).",
    )
    p.add_argument(
        "--presentation-tts-model",
        default=None,
        help="OpenRouter TTS model name override (default: openai/gpt-4o-mini-tts-2025-12-15).",
    )
    p.add_argument(
        "--presentation-video",
        action="store_true",
        help="Presentation mode: render video from PDF slides and audio.",
    )
    p.add_argument(
        "--presentation-exclude-slides",
        default="",
        help="Comma-separated slide numbers or ranges to exclude (e.g., 2,5,10-12).",
    )
    p.add_argument(
        "--presentation-image-model",
        default=None,
        help="Presentation mode: OpenRouter image model override (default: openai/gpt-5.4-image-2).",
    )
    p.add_argument(
        "--no-image",
        action="store_true",
        help="Presentation mode: disable slide image generation.",
    )
    p.add_argument(
        "--presentation-citations",
        action="store_true",
        help="Presentation mode: include Harvard-style citations from MCP paper tools.",
    )
    p.add_argument(
        "--disable-general-knowledge-citation",
        action="store_true",
        help="Presentation mode: remove/forbid the marker 'General background knowledge' in slide text.",
    )
    p.add_argument(
        "-j",
        "--json",
        dest="json_path",
        default="book_structure.json",
        help="Cesta k JSON se strukturou (default: book_structure.json; paper uses paper_structure.json).",
    )
    p.add_argument(
        "-o",
        "--out-dir",
        default="out",
        help="Vystupni adresar pro .tex/.pdf/.md a sekce (default: ./out).",
    )
    p.add_argument(
        "--kb-dir",
        default=None,
        help="Volitelny adresar s PDF/DOCX/PPTX/MD/TXT pro RAG znalostni databazi. Pokud neni zadan, RAG se nepouzije.",
    )
    p.add_argument(
        "--kb1-dir",
        default=None,
        help="KB1 adresar pro proposal/reviewer mode (grant requirements / norms).",
    )
    p.add_argument(
        "--kb2-dir",
        default=None,
        help="KB2 adresar pro proposal/reviewer mode (project background / thesis).",
    )
    p.add_argument(
        "--rebuild-kb",
        action="store_true",
        help="Vynutit rebuild znalostni databaze (pokud je --kb-dir).",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Pokračovat z dříve uložených částečných výsledků v --out-dir.",
    )

    # Output format toggles
    p.add_argument("--no-tex", action="store_true", help="Negenerovat .tex výstup.")
    p.add_argument("--no-pdf", action="store_true", help="Negenerovat .pdf výstup.")
    p.add_argument("--no-md", action="store_true", help="Negenerovat .md výstup.")
    p.add_argument(
        "--docx",
        action="store_true",
        help="Ulozit finalni vystup take jako .docx (kde je podporovan pandoc export).",
    )

    # Non-interactive overrides
    g = p.add_mutually_exclusive_group()
    g.add_argument("--use-json", action="store_true", help="Pokud JSON existuje, použij jej bez dotazu.")
    g.add_argument("--use-txt", action="store_true", help="Pokud JSON existuje, vygenerovat nový z TXT bez dotazu.")

    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mode == "paper" and getattr(args, "json_path", None) == "book_structure.json":
        args.json_path = "paper_structure.json"
    if args.mode == "presentation":
        if getattr(args, "presentation_input", None) and args.input == "book_input.txt":
            args.input = args.presentation_input
        if getattr(args, "json_path", None) == "book_structure.json":
            args.json_path = "presentation_structure.json"
    if args.mode == "proposal" and getattr(args, "max_iters", None) is None:
        args.max_iters = 3
    if getattr(args, "fail_fast_schema", False):
        os.environ["AUTOGENBOOK_FAIL_FAST_SCHEMA"] = "1"
    if getattr(args, "llm_base_url", None):
        os.environ["AUTOGENBOOK_LLM_BASE_URL"] = str(args.llm_base_url)
    out_dir = Path(args.out_dir).expanduser().resolve()
    args.run_ctx = RunContext.create(out_dir=out_dir, mode=args.mode)
    return run_orchestrator(args)


if __name__ == "__main__":
    raise SystemExit(main())

