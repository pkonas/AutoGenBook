# Proposal mode design (Phase 2)

This document defines the CLI surface, data contracts, runtime pipeline, and prompt file layout for the new `proposal` mode, grounded in the current repository structure and conventions.

---

## A) CLI design

Target: integrate into `main.py` `argparse` in the same style as existing modes (`book`, `paper`, `scientist`).

Required flags (proposal-only):
- `--mode proposal`
- `--kb1-dir PATH` (grant requirements docs)
- `--kb2-dir PATH` (project background docs)
- `--proposal-input PATH` (defaults to `proposal_input.txt`)
- `--max-iters N` (applies to both loops: outline loop steps 5–8 and final loop step 10)
- `--out-dir PATH` (artifact output directory)

Compatibility with existing conventions:
- Keep `--out-dir` as a shared argument (already used by all modes).
- Keep `--rebuild-kb`, `--enable-web-rag`, and `--web-rag-k` consistent with existing flags if proposal chooses to support web retrieval.
- Keep `--resume`, `--no-tex`, `--no-pdf`, `--no-md` behavior consistent if proposal mode reuses output conversions.

Example CLI usage:
```bash
python main.py --mode proposal \
  --proposal-input proposal_input.txt \
  --kb1-dir ./grant_requirements \
  --kb2-dir ./project_background \
  --max-iters 2 \
  --out-dir out_proposal
```

---

## B) Data contracts (Pydantic models)

Implementation location:
- `autogenbook/schemas/proposal.py` (new file)
- Reuse `StrictBaseModel`, `RidStr`, and `CiteKeyStr` from `autogenbook/schemas`.

### 1) LLM1 output (architect)
Fields:
- Compliance checklist with `REQ-*` ids and KB1 evidence references.
- `meta_prompt_for_llm2`.
- `format_requirements` (output formats + limits).

### 2) LLM2 outline output (researcher-outline)
Fields:
- Structured outline tree with `SEC-*` ids.
- Mapping `SEC -> REQ` ids.
- Annex list.

### 3) LLM3 review output (opponent-outline)
Fields:
- Compliance result.
- Missing-info `user_questions` (structured).
- `instructions_to_llm1` and `instructions_to_llm2`.
- `should_iterate` + `stop_reason`.

### 4) `sources.json` format
Fields:
- Normalized list of sources with ISO 690-ready metadata (authors, title, year, url/doi/arxiv, accessed date).

### Proposed schema (Python)
```python
from autogenbook.schemas.proposal import (
    ProposalLLM1Output,
    ProposalOutlineOutput,
    ProposalReviewOutput,
    ProposalSources,
)
```

Key model names and fields (see `autogenbook/schemas/proposal.py`):
- `ProposalLLM1Output`
  - `compliance_checklist: list[ComplianceRequirement]`
  - `meta_prompt_for_llm2: str`
  - `format_requirements: FormatRequirements`
- `ProposalOutlineOutput`
  - `outline: ProposalOutlineGraph`
  - `section_requirement_map: list[SectionRequirementMap]`
  - `annexes: list[AnnexItem]`
- `ProposalReviewOutput`
  - `compliance_status: Literal["pass","fail","needs_info"]`
  - `user_questions: list[UserQuestion]`
  - `instructions_to_llm1: list[str]`
  - `instructions_to_llm2: list[str]`
  - `should_iterate: bool`
  - `stop_reason: str`
- `ProposalSources`
  - `sources: list[SourceEntry]` with ISO 690 metadata fields

---

## C) Runtime pipeline (steps 1–11)

Implementation target: a new proposal pipeline module (e.g., `autogenbook/pipelines/proposal_pipeline.py`) that mirrors `book_pipeline.py` and `paper_pipeline.py` patterns.

### Steps 1–4: input and KB construction
1) Read `--kb1-dir` and build KB1 using `rag_kb.KnowledgeBase.build_from_directory`.
2) Read `--kb2-dir` and build KB2 using `rag_kb.KnowledgeBase.build_from_directory`.
3) Read `--proposal-input` as plain text. Language of the proposal is derived from this file (same behavior as `book_input.txt` / `paper_input.txt`).
4) Persist KB indexes for traceability:
   - Use `autogenbook/retrieval/kb_citations.build_kb_index` to write `kb1_sources.json` and `kb2_sources.json` under `--out-dir`.

### Steps 5–8: outline loop (LLM1 -> LLM2 -> LLM3)
5) LLM1 (architect) builds:
   - `ProposalLLM1Output` from KB1 (grant requirements).
   - Output includes `compliance_checklist` and `meta_prompt_for_llm2`.
6) LLM2 (researcher-outline) builds:
   - `ProposalOutlineOutput` using `meta_prompt_for_llm2` as SYSTEM prompt.
   - Retrieval context includes both KB1 + KB2 excerpts via `RetrievalManager` (two KBs, merged results).
7) LLM3 (opponent-outline) validates:
   - Compliance against KB1.
   - Scientific consistency with KB2.
   - If info missing and not found in KB1/KB2/tools, return structured `user_questions`.
   - Generate `instructions_to_llm1` and `instructions_to_llm2`.
   - If user refuses to answer, record refusal in `proposal_input.txt` and do not re-ask.
8) Loop steps 5–7 until compliant or `--max-iters` reached.

Missing-info strategy:
- First attempt: retrieve from KB1/KB2 using `RetrievalManager.retrieve`.
- Optional: allow web retrieval via `MCPPaperRetriever` / `TavilyRetriever` if `--enable-web-rag` is set.
- If still missing: prompt user; append responses (or refusal) to `proposal_input.txt`.

### Step 9: writer loop (LLM4)
- LLM4 (researcher-writer) writes proposal section-by-section, similar to the leaf-node loop in `paper_pipeline.run_paper`.
- Each section uses:
  - outline context via `utils.generate_outline_text`.
  - KB1+KB2 retrieval items (merged) and optional web retrieval.
- Save incremental artifacts:
  - `draft.md` (aggregate)
  - `sections/*.md` or `sections/*.tex` (if LaTeX-first path is chosen)
  - `sources.json` (normalized ISO 690 metadata)
- No fabricated sources: citations must map to KB1/KB2 or tool outputs; otherwise LLM3 must ask for missing info.

### Step 10: final review loop (LLM5)
- LLM5 validates the full draft against KB1/KB2.
- If issues found, revise and re-check up to `--max-iters`.
- Persist revision artifacts:
  - `final.md` (primary)
  - Optional format conversions (PDF/LaTeX) if required by KB1.

### Step 11: final evaluation report
- Save a final report detailing:
  - which requirements are satisfied
  - what remains improvable (formal + scientific)
- Suggested artifact: `final_review_report.md` or `final_review.json`.

### ISO 690 and references
- The list of references in the final output must be ISO 690 with heading `Použitá literatura`.
- Implementation note based on current repo:
  - `book_builder._apply_iso690_citations_v3` implements ISO 690 formatting.
  - The alias `_apply_iso690_citations` is reassigned to `_apply_bibtex_citations` at the end of `book_builder.py`, so proposal mode should call `_apply_iso690_citations_v3` directly or rework the alias in a later phase.

---

## D) Prompt files

Prompts must be editable by users and loaded at runtime from markdown files.

Proposed directory:
- `prompts/proposal/`

Proposed files:
- `prompts/proposal/llm1_architect_system.md`
- `prompts/proposal/llm1_architect_user.md`
- `prompts/proposal/llm2_researcher_outline_system.md`
- `prompts/proposal/llm2_researcher_outline_user.md`
- `prompts/proposal/llm3_opponent_outline_system.md`
- `prompts/proposal/llm3_opponent_outline_user.md`
- `prompts/proposal/llm4_researcher_writer_system.md`
- `prompts/proposal/llm4_researcher_writer_user.md`
- `prompts/proposal/llm5_opponent_final_system.md`
- `prompts/proposal/llm5_opponent_final_user.md`

Loading strategy:
- Read prompt files via `Path.read_text(encoding="utf-8")`.
- Render placeholders with `autogenbook.prompts.agent_prompts.render` to preserve existing templating rules and literal braces.
- Store loaded prompt text in `AgentContext` or a small helper module (e.g., `autogenbook/prompts/proposal_loader.py`) in a later phase.

---

## Reusable components from the current repo
- LLM client: `openrouter_llm.OpenRouterLLM` + `LLMConfig`.
- RAG: `rag_kb.KnowledgeBase` + `autogenbook/retrieval/manager.RetrievalManager`.
- Outline/graph: `autogenbook/graph/doc_graph.py` + `utils.generate_outline_text`.
- Section writer loop: `paper_pipeline.run_paper` (leaf-node loop + retrieval + revision).
- Output conversion: `book_builder.compile_pdf`, `book_builder.export_markdown`.
- Logging: `autogenbook/agents/io_log.write_agent_io`.
