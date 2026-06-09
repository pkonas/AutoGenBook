from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple, List, Dict

from rag_kb import SUPPORTED_EXTS, KnowledgeBase
from autogenbook.retrieval.kb_citations import build_kb_index
from autogenbook.retrieval.manager import RetrievalManager
from autogenbook.retrieval.types import RetrievalItem
from autogenbook.retrieval.sanitize import sanitize_context_text
from openrouter_llm import LLMConfig, OpenRouterLLM
from autogenbook.llm_usage import log_usage, write_run_meta
from autogenbook.pipelines.proposal_pipeline import (
    _convert_with_pandoc,
    _resolve_model_override,
    _resolve_value_override,
)
from autogenbook.prompts.reviewer_loader import load_reviewer_prompts
from autogenbook.prompts.registry import set_prompt_registry
from autogenbook.state import RunContext


def _log(logger: Any, message: str) -> None:
    if logger is not None:
        try:
            logger.info(message)
            return
        except Exception:
            pass
    print(message)


def _dir_has_supported_files(path: Path) -> bool:
    if path is None or not path.exists() or not path.is_dir():
        return False
    for entry in path.rglob("*"):
        if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTS:
            return True
    return False


def _resolve_dir(raw: Optional[str]) -> Optional[Path]:
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _kb_stats(kb: Optional[KnowledgeBase]) -> Tuple[int, int]:
    if kb is None:
        return 0, 0
    chunks = list(getattr(kb, "chunks", []) or [])
    sources = {c.source_path for c in chunks if getattr(c, "source_path", None)}
    return len(chunks), len(sources)


def _kb_item_from_chunk(chunk: Any) -> RetrievalItem:
    source_path = Path(getattr(chunk, "source_path", "") or "")
    return RetrievalItem(
        rid=getattr(chunk, "rid", "") or "",
        kind="kb",
        source=source_path.name,
        loc=getattr(chunk, "loc", "") or "",
        score=0.0,
        cite_key=getattr(chunk, "cite_key", "") or "",
        text=getattr(chunk, "text", "") or "",
        url=None,
        title=source_path.name,
    )


def _collect_kb1_context(
    kb1_retriever: RetrievalManager,
    query_hint: Optional[str] = None,
    *,
    k: int = 8,
) -> Tuple[str, int]:
    if kb1_retriever is None:
        return "(none)", 0

    queries = []
    if query_hint:
        compact_hint = " ".join(query_hint.split())
        if len(compact_hint) > 300:
            compact_hint = compact_hint[:300]
        if compact_hint:
            queries.append(compact_hint)
    queries.extend(
        [
            "requirements norms regulations compliance evaluation criteria mandatory sections",
            "rules policies standards regulations legal ethics GDPR",
            "pozadavky normy predpisy regulace soulad kriteria hodnoceni povinne sekce",
            "pravidla zasady standardy etika gdpr zakon vyhlaska",
        ]
    )

    items: List[RetrievalItem] = []
    seen = set()
    for query in queries:
        retrieved = kb1_retriever.retrieve(
            query, k=min(max(k, 1), 10), allow_web=False, diversify_sources=True
        )
        for item in retrieved:
            key = item.rid or item.cite_key or f"{item.source}:{item.loc}"
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            if len(items) >= k:
                break
        if len(items) >= k:
            break

    if not items and kb1_retriever.local_kb is not None:
        try:
            kb1_retriever.local_kb._ensure_chunk_ids()
        except Exception:
            pass
        for chunk in kb1_retriever.local_kb.chunks[:k]:
            items.append(_kb_item_from_chunk(chunk))

    context = kb1_retriever.format_context(items) if items else "(none)"
    return context, len(items)


def _collect_pdf_direct_text(
    kb: Optional[KnowledgeBase],
    max_chars: int = 8000,
) -> Tuple[str, int]:
    if kb is None or not getattr(kb, "chunks", None):
        return "", 0
    remaining = max_chars
    blocks: List[str] = []
    pdf_sources: List[str] = []
    current_source: Optional[str] = None
    for chunk in kb.chunks:
        source_path = getattr(chunk, "source_path", "")
        if not source_path or Path(source_path).suffix.lower() != ".pdf":
            continue
        if source_path not in pdf_sources:
            pdf_sources.append(source_path)
        if current_source != source_path:
            current_source = source_path
            header = f"[PDF:{Path(source_path).name}]"
            if len(header) + 1 > remaining:
                break
            blocks.append(header)
            remaining -= len(header) + 1
        text = sanitize_context_text(str(getattr(chunk, "text", "")).strip())
        if not text:
            continue
        if len(text) + 1 > remaining:
            blocks.append(text[:remaining])
            remaining = 0
            break
        blocks.append(text)
        remaining -= len(text) + 1
        if remaining <= 0:
            break
    return "\n".join(blocks).strip(), len(pdf_sources)


def _log_llm_usage(
    out_dir: Path,
    llm: Any,
    label: str,
    usage: Optional[Dict[str, int]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if llm is None or not hasattr(llm, "get_last_usage"):
        return
    usage = usage or llm.get_last_usage()
    if not usage:
        return
    if hasattr(llm, "format_usage_line"):
        try:
            print(llm.format_usage_line(usage, label=label))
        except Exception:
            pass
    if isinstance(llm, OpenRouterLLM):
        try:
            log_usage(out_dir, label, llm, usage, extra or {})
        except Exception:
            pass


_REVIEWER_KB_CACHE_BASE: Optional[Path] = None
_REVIEWER_OUT_DIR: Optional[Path] = None
_REVIEWER_FORCE_REBUILD: bool = False
_REVIEWER_LOGGER: Any = None


def _configure_reviewer_kb(out_dir: Path, force_rebuild: bool, logger: Any) -> None:
    global _REVIEWER_KB_CACHE_BASE, _REVIEWER_OUT_DIR, _REVIEWER_FORCE_REBUILD, _REVIEWER_LOGGER
    _REVIEWER_KB_CACHE_BASE = out_dir / ".kb_cache"
    _REVIEWER_OUT_DIR = out_dir
    _REVIEWER_FORCE_REBUILD = force_rebuild
    _REVIEWER_LOGGER = logger


def _kb_cache_dir(kind: str) -> Path:
    base = _REVIEWER_KB_CACHE_BASE or (Path.cwd() / ".kb_cache")
    return base / f"reviewer_{kind}"


def build_or_load_kb1(kb1_dir: Optional[Path]) -> Tuple[bool, Optional[RetrievalManager]]:
    if kb1_dir is None or not _dir_has_supported_files(kb1_dir):
        _log(_REVIEWER_LOGGER, "[REVIEWER] KB1 missing or empty; skipping KB1.")
        return False, None
    kb1 = KnowledgeBase.build_from_directory(
        kb1_dir,
        cache_dir=_kb_cache_dir("kb1"),
        force_rebuild=_REVIEWER_FORCE_REBUILD,
    )
    chunk_count, source_count = _kb_stats(kb1)
    _log(
        _REVIEWER_LOGGER,
        f"[REVIEWER] KB1 built: {chunk_count} chunks from {source_count} sources.",
    )
    if chunk_count == 0:
        _log(
            _REVIEWER_LOGGER,
            "[REVIEWER] KB1 has 0 chunks. Check file formats or enable OCR.",
        )
    if _REVIEWER_OUT_DIR is not None:
        (_REVIEWER_OUT_DIR / "kb1_sources.json").write_text(
            json.dumps(build_kb_index(kb1), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    retriever = RetrievalManager(local_kb=kb1, enable_web=False)
    return True, retriever


def build_or_load_kb2(kb2_dir: Optional[Path]) -> RetrievalManager:
    if kb2_dir is None or not kb2_dir.exists() or not kb2_dir.is_dir():
        raise FileNotFoundError("--kb2-dir is required and must be an existing directory.")
    if not _dir_has_supported_files(kb2_dir):
        raise ValueError(
            "--kb2-dir contains no supported documents (.pdf/.docx/.pptx/.md/.txt)."
        )
    kb2 = KnowledgeBase.build_from_directory(
        kb2_dir,
        cache_dir=_kb_cache_dir("kb2"),
        force_rebuild=_REVIEWER_FORCE_REBUILD,
    )
    chunk_count, source_count = _kb_stats(kb2)
    _log(
        _REVIEWER_LOGGER,
        f"[REVIEWER] KB2 built: {chunk_count} chunks from {source_count} sources.",
    )
    if chunk_count == 0:
        _log(
            _REVIEWER_LOGGER,
            "[REVIEWER] KB2 has 0 chunks. Check file formats or enable OCR.",
        )
    if _REVIEWER_OUT_DIR is not None:
        (_REVIEWER_OUT_DIR / "kb2_sources.json").write_text(
            json.dumps(build_kb_index(kb2), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return RetrievalManager(local_kb=kb2, enable_web=False)


def resolve_prompt1(
    kb1_available: bool,
    prompt1_default_path: Path,
    architect_prompt_path: Path,
    kb1_retriever: Optional[RetrievalManager],
    model_client: OpenRouterLLM,
    out_dir: Path,
    *,
    usage_label: str = "reviewer_llm1",
) -> str:
    prompt1_default = prompt1_default_path.read_text(encoding="utf-8")
    if not kb1_available or kb1_retriever is None:
        _log(_REVIEWER_LOGGER, "[REVIEWER] prompt1: using default (KB1 missing).")
        return prompt1_default

    if not architect_prompt_path.exists():
        raise FileNotFoundError(f"Architect prompt not found: {architect_prompt_path}")

    architect_prompt = architect_prompt_path.read_text(encoding="utf-8")
    kb1_ctx, kb1_hits = _collect_kb1_context(kb1_retriever)
    if kb1_hits == 0:
        _log(
            _REVIEWER_LOGGER,
            "[REVIEWER] KB1 retrieval returned 0 items for architect prompt.",
        )

    user_prompt = (
        "KB1_CONTEXT:\n"
        f"{kb1_ctx}\n\n"
        "TASK:\n"
        "Generate ONLY the final system prompt for LLM2. Do not add commentary.\n"
    )
    generated = model_client.chat(
        [
            {"role": "system", "content": architect_prompt},
            {"role": "user", "content": user_prompt},
        ],
        allow_tools=False,
    )
    _log_llm_usage(out_dir, model_client, usage_label, extra={"stage": "prompt1"})
    prompt1_text = (generated or "").strip()
    if not prompt1_text:
        raise RuntimeError("LLM1 returned empty prompt1.")

    out_path = out_dir / "reviewer_prompt1_generated.md"
    out_path.write_text(prompt1_text, encoding="utf-8")
    _log(_REVIEWER_LOGGER, f"[REVIEWER] prompt1: generated from KB1 -> {out_path}")
    return prompt1_text


def run_reviewer(args: Any, run_ctx: Optional[RunContext], logger: Any) -> int:
    out_dir = Path(args.out_dir).expanduser().resolve()
    if run_ctx is None:
        run_ctx = RunContext.create(out_dir=out_dir, mode="reviewer")

    out_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    error: Optional[str] = None
    status: str = "ok"

    prompts = load_reviewer_prompts()
    set_prompt_registry("reviewer", prompts)

    kb1_dir = _resolve_dir(getattr(args, "kb1_dir", None))
    kb2_dir = _resolve_dir(getattr(args, "kb2_dir", None))

    _configure_reviewer_kb(out_dir, bool(getattr(args, "rebuild_kb", False)), logger)

    try:
        kb2_retriever = build_or_load_kb2(kb2_dir)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    kb1_available, kb1_retriever = build_or_load_kb1(kb1_dir)
    if kb1_available:
        _log(logger, f"[REVIEWER] KB1 dir: {kb1_dir}")
    else:
        _log(logger, "[REVIEWER] kb1_dir missing or empty; proceeding without KB1.")

    _log(logger, f"[REVIEWER] KB2 dir: {kb2_dir}")
    _log(logger, f"[REVIEWER] KB2 retriever ready: {kb2_retriever is not None}")

    prompt_dir = Path(__file__).resolve().parents[2] / "prompts" / "reviewer"
    prompt1_default_path = prompt_dir / "prompt1_default.md"
    architect_prompt_path = prompt_dir / "architect_prompt.md"
    prompt2_path = prompt_dir / "prompt2.md"
    if not prompt1_default_path.exists():
        print(f"Error: prompt1_default not found: {prompt1_default_path}", file=sys.stderr)
        return 2
    if not architect_prompt_path.exists():
        print(f"Error: architect_prompt not found: {architect_prompt_path}", file=sys.stderr)
        return 2
    if not prompt2_path.exists():
        print(f"Error: prompt2 not found: {prompt2_path}", file=sys.stderr)
        return 2

    try:
        model_llm1 = _resolve_model_override(
            args, "reviewer_llm1_model", "AUTOGENBOOK_REVIEWER_LLM1_MODEL"
        )
        model_llm2 = _resolve_model_override(
            args, "reviewer_llm2_model", "AUTOGENBOOK_REVIEWER_LLM2_MODEL"
        )
        base_llm1 = _resolve_value_override(
            args, "reviewer_llm1_base_url", "AUTOGENBOOK_REVIEWER_LLM1_BASE_URL"
        )
        base_llm2 = _resolve_value_override(
            args, "reviewer_llm2_base_url", "AUTOGENBOOK_REVIEWER_LLM2_BASE_URL"
        )

        def _build_config(model_override: Optional[str], base_url_override: Optional[str]) -> Optional[LLMConfig]:
            if model_override or base_url_override:
                base = LLMConfig()
                return LLMConfig(
                    model=model_override or base.model,
                    temperature=base.temperature,
                    max_tokens=base.max_tokens,
                    input_cost_per_million=base.input_cost_per_million,
                    output_cost_per_million=base.output_cost_per_million,
                    base_url=base_url_override,
                )
            return None

        llm1 = OpenRouterLLM(_build_config(model_llm1, base_llm1))
        llm2 = OpenRouterLLM(_build_config(model_llm2, base_llm2))
        prompt1_text = resolve_prompt1(
            kb1_available,
            prompt1_default_path,
            architect_prompt_path,
            kb1_retriever,
            llm1,
            out_dir,
        )
    except Exception as exc:
        error = str(exc)
        status = "error"
        print(f"Error: failed to resolve prompt1: {exc}", file=sys.stderr)
        return 2

    try:
        prompt2_text = prompt2_path.read_text(encoding="utf-8")
    except Exception as exc:
        error = str(exc)
        status = "error"
        print(f"Error: failed to read prompt2: {exc}", file=sys.stderr)
        return 2

    kb2_items = kb2_retriever.retrieve(prompt2_text or "review", k=6, allow_web=False)
    kb2_ctx = kb2_retriever.format_context(kb2_items) if kb2_items else "(none)"
    kb1_ctx = "(none)"
    if kb1_available and kb1_retriever is not None:
        kb1_ctx, kb1_hits = _collect_kb1_context(kb1_retriever, query_hint=prompt2_text)
        if kb1_hits == 0:
            _log(logger, "[REVIEWER] KB1 retrieval returned 0 items for reviewer prompt.")

    direct_pdf_text = ""
    if bool(getattr(args, "reviewer_direct_pdf", False)):
        direct_pdf_text, pdf_sources = _collect_pdf_direct_text(
            getattr(kb2_retriever, "local_kb", None)
        )
        if direct_pdf_text:
            _log(
                logger,
                f"[REVIEWER] Direct PDF context included ({len(direct_pdf_text)} chars, {pdf_sources} files).",
            )
        else:
            _log(logger, "[REVIEWER] Direct PDF context requested but no PDF text found.")

    llm2_user_prompt = f"{prompt2_text.strip()}\n\n"
    if direct_pdf_text:
        llm2_user_prompt += f"KB2_PDF_DIRECT:\n{direct_pdf_text}\n\n"
    llm2_user_prompt += (
        "KB2_CONTEXT:\n"
        f"{kb2_ctx}\n\n"
        "KB1_CONTEXT:\n"
        f"{kb1_ctx}\n\n"
        "TRUTHFULNESS:\n"
        "- Ground the review in KB2 (and KB1 if available).\n"
        "- If information is missing, explicitly state it and avoid guessing.\n"
    )
    try:
        review_output = llm2.chat(
            [
                {"role": "system", "content": prompt1_text},
                {"role": "user", "content": llm2_user_prompt},
            ],
            allow_tools=True,
        )
        _log_llm_usage(out_dir, llm2, "reviewer_llm2", extra={"stage": "review"})
    except Exception as exc:
        error = str(exc)
        status = "error"
        print(f"Error: LLM2 reviewer call failed: {exc}", file=sys.stderr)
        return 2

    review_path = out_dir / "review_final.md"
    review_path.write_text(str(review_output or "").strip(), encoding="utf-8")
    _log(logger, f"[REVIEWER] review_final.md written: {review_path}")

    formats = ["markdown"]
    if not bool(getattr(args, "no_pdf", False)):
        formats.append("pdf")
    if not bool(getattr(args, "no_tex", False)):
        formats.append("tex")
    if bool(getattr(args, "docx", False)):
        formats.append("docx")
    if formats:
        try:
            _convert_with_pandoc(review_path, out_dir, formats, basename="review_final")
            _log(logger, "[REVIEWER] Export conversion finished.")
        except Exception as exc:
            _log(logger, f"[REVIEWER] Export conversion failed: {exc}")

    if llm1 is not None:
        try:
            print(f"[TOKENS] LLM1 total tokens: {llm1.get_total_tokens()}")
        except Exception:
            pass
    if llm2 is not None:
        try:
            print(f"[TOKENS] LLM2 total tokens: {llm2.get_total_tokens()}")
        except Exception:
            pass
    total_tokens = 0
    for llm in (llm1, llm2):
        if llm is None or not hasattr(llm, "get_total_tokens"):
            continue
        try:
            total_tokens += int(llm.get_total_tokens())
        except Exception:
            pass
    if total_tokens:
        print(f"[TOKENS] Celkem spotrebovano tokenu: {total_tokens}")
    total_cost = 0.0
    cost_known = False
    for llm in (llm1, llm2):
        if llm is None or not hasattr(llm, "get_total_cost_usd"):
            continue
        try:
            cost_val = llm.get_total_cost_usd()
            if cost_val is not None:
                total_cost += float(cost_val)
                cost_known = True
        except Exception:
            pass
    if cost_known:
        print(f"[COST] Celkova cena: ${total_cost:.6f}")

    try:
        models = {
            "llm1": llm1.config.model if llm1 is not None else None,
            "llm2": llm2.config.model if llm2 is not None else None,
        }
        token_totals = {
            "llm1": llm1.get_total_tokens() if llm1 is not None else 0,
            "llm2": llm2.get_total_tokens() if llm2 is not None else 0,
        }
        cost_totals = {
            "llm1": llm1.get_total_cost_usd() if llm1 is not None else None,
            "llm2": llm2.get_total_cost_usd() if llm2 is not None else None,
        }
        write_run_meta(
            run_ctx=run_ctx,
            args=args,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            status=status,
            error=error,
            models=models,
            token_totals=token_totals,
            cost_totals_usd=cost_totals,
        )
    except Exception:
        pass

    _log(logger, "[REVIEWER] Reviewer mode initialized (stub).")
    return 0
