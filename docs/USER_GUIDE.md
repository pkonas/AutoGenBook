# User Guide

## Overview

AutoGenBook runs as a CLI that builds long-form documents through mode-specific pipelines (book, paper, presentation, scientist, proposal, reviewer) and writes artifacts to an output directory. (`main.py:parse_args`, `autogenbook/orchestrator.py:run`, `autogenbook/pipelines/*`, `autogenbook/state.py:RunContext`)

## Concepts and terminology

### Document graph

Book and paper modes build a directed graph of sections where each node has a title, summary, and page budget, and leaf nodes are written as sections. (`book_builder.py:build_graph_from_book_json`, `autogenbook/graph/doc_graph.py`, `autogenbook/pipelines/paper_pipeline.py:_build_graph_from_paper_json`)

### Structure snapshots

The graph is serialized to `structure_graph.json` in `--out-dir` so you can resume or inspect runs. (`autogenbook/state.py:RunContext`, `autogenbook/graph/doc_graph.py:save_graph_json`, `book_builder.py:generate_contents`)

### Knowledge base (KB) and RAG

A local knowledge base is built from files under `--kb-dir` (PDF/DOCX/PPTX/MD/TXT) and retrieved with BM25. (`rag_kb.py:KnowledgeBase`, `rag_kb.py:SUPPORTED_EXTS`)

Retrieved chunks are formatted into prompt-ready context blocks with stable `RID` and `cite_key` identifiers. (`rag_kb.py:KnowledgeBase.format_context`, `autogenbook/retrieval/manager.py:format_context`)

### Retrieval items

All retrieval sources (KB, web search, run artifacts) are normalized into `RetrievalItem` objects with `rid`, `cite_key`, and metadata used for citations. (`autogenbook/retrieval/types.py:RetrievalItem`, `autogenbook/retrieval/manager.py:retrieve`)

### MCP and web search

When `--enable-web-rag` is set, retrieval may use MCP paper tools or Tavily search, depending on availability and API keys. (`autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever`, `autogenbook/retrieval/tavily.py:TavilyRetriever`, `autogenbook/retrieval/manager.py:RetrievalManager`)

### Citations

LaTeX citations are extracted and normalized; BibTeX entries are generated from retrieval items when needed. (`autogenbook/citations/extract.py:extract_citations`, `autogenbook/citations/ledger.py:CitationLedger`, `book_builder.py:_apply_bibtex_citations`)

### Audit

LaTeX audits check for unknown citations, missing figures, and numeric claims without evidence, producing `audit_report.json`. (`autogenbook/audit/latex_auditor.py:audit_latex`, `autogenbook/audit/report.py:AuditReport`)

## Workflows by mode

### Book mode

1. Read TXT or JSON structure and build a graph. (`book_builder.py:generate_book_json_from_txt`, `book_builder.py:build_graph_from_book_json`)
2. Subdivide oversized sections into smaller nodes. (`book_builder.py:subdivide_graph`)
3. Generate leaf sections with retrieval context, optional review, and length control. (`book_builder.py:generate_contents`, `autogenbook/agents/book_section_writer.py:BookSectionWriterAgent`, `autogenbook/agents/book_section_reviewer.py:BookSectionReviewerAgent`, `autogenbook/length_control.py:enforce_section_length`)
4. Assemble LaTeX and optionally compile PDF and export Markdown. (`book_builder.py:build_latex_document`, `book_builder.py:compile_pdf`, `book_builder.py:export_markdown`)

Outputs include `structure_graph.json`, `sections/*.tex`, a `.tex` document named from the title, optional `.pdf` and `.md`, and usage logs. (`autogenbook/state.py:RunContext`, `book_builder.py:generate_contents`, `book_builder.py:build_latex_document`, `book_builder.py:export_markdown`, `autogenbook/llm_usage.py:log_usage`)

### Paper mode

1. Build a paper JSON from TXT or load an existing structure. (`autogenbook/pipelines/paper_pipeline.py:_build_paper_json`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)
2. Subdivide the paper graph to leaf sections. (`autogenbook/pipelines/paper_pipeline.py:_subdivide_paper_graph`)
3. Generate leaf sections with retrieval and optional web refinement. (`autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/agents/paper_writer.py:PaperSectionWriterAgent`)
4. Assemble LaTeX and manage citations (`refs.bib` for BibTeX mode). (`autogenbook/pipelines/paper_pipeline.py:_build_paper_latex`, `autogenbook/citations/ledger.py:CitationLedger`)

Outputs include `structure_graph.json`, `sections/*.tex`, `related_work.json`, a `.tex` paper, optional PDF/Markdown, and `refs.bib` when using BibTeX citations. (`autogenbook/pipelines/paper_pipeline.py:run_paper`)

### Presentation mode

1. Build a presentation JSON from TXT or load an existing structure. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
2. Subdivide the slide graph to leaf slides. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
3. Generate slide bodies with retrieval context and length control. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`, `autogenbook/agents/presentation_slide_writer.py:PresentationSlideWriterAgent`, `autogenbook/length_control.py:enforce_section_length`)
4. Assemble a Markdown deck and optionally export to PPTX/Beamer or generate narration/audio/video. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`, `autogenbook/presentation_export.py:md_to_pptx`, `autogenbook/presentation_export.py:md_to_beamer_tex`)

Outputs include `structure_graph.json`, `slides/*.md`, `images/*.png`, a Markdown deck named from the title, optional `.pptx` / `.tex` / `.pdf`, narration `.md/.json`, audio `.wav`, and video `.mp4`. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`)

If `--presentation-citations` is enabled, slide text may include Harvard-style citations from MCP paper tools (omitted when MCP tools are unavailable). (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`)

### Scientist mode

Scientist mode runs a toy experiment template, analyzes results, writes a paper draft, and applies review/revision loops. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`, `autogenbook/templates/toy_classification/run_experiment.py:main`, `autogenbook/agents/*`)

It can apply safe code patches between iterations with a strict diff parser and safety checks. (`autogenbook/runner/patch_apply.py:apply_unified_diff`)

Outputs include `experiments/<run_id>/metrics.json`, `review.json`, `audit_report.json`, and LaTeX artifacts. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`)

### Proposal mode

Proposal mode requires two KB directories (`--kb1-dir`, `--kb2-dir`) and requires MCP paper tools for citations. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

The pipeline runs a multi-LLM loop (LLM1-LLM5) to extract requirements, build an outline, draft sections with MCP citations, and perform final review. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/schemas/proposal.py`)

Outputs include per-section artifacts (`sections/*/section.md`, `citations.json`, `used_sources.json`, `tool_calls.json`), a final proposal Markdown, and optional conversions via pandoc. (`autogenbook/pipelines/proposal_pipeline.py:_write_section_artifacts`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

### Reviewer mode

Reviewer mode requires KB2 and optionally KB1 to generate an opponent-style review in Markdown, with optional PDF/TeX conversion via pandoc. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)

If `--reviewer-direct-pdf` is set, direct PDF text from KB2 is embedded into the review prompt. (`autogenbook/pipelines/reviewer_pipeline.py:_collect_pdf_direct_text`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)

## Usage variations

### Minimal/default usage

- Provide only `--input` (book/paper) and `--out-dir`; the pipeline will generate a structure JSON and sections. (`main.py:parse_args`, `book_builder.py:generate_book_json_from_txt`, `autogenbook/pipelines/paper_pipeline.py:_build_paper_json`)

### Advanced usage

- Add `--kb-dir` to force grounding in local documents. (`book_builder.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `rag_kb.py:KnowledgeBase`)
- Enable web retrieval with `--enable-web-rag` and ensure MCP gateway tools or Tavily are available. (`autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever`, `autogenbook/retrieval/tavily.py:TavilyRetriever`, `autogenbook/retrieval/manager.py:RetrievalManager`)
- Use `--resume` to reuse existing section files and continue a run. (`book_builder.py:generate_contents`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)
- Turn on audits with `--audit` (paper/scientist/proposal) or `--audit-book` (book). (`autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/pipelines/book_pipeline.py:run_book`)

### Dev/staging/prod style runs

- Dev: reduce cost by forcing a small model (`AUTOGENBOOK_FORCE_MINI_MODEL=1`). (`openrouter_llm.py:OpenRouterLLM.__init__`)
- Staging: enable audits in `warn` mode to surface issues without failing. (`autogenbook/audit/latex_auditor.py:audit_latex`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)
- Prod: use `--audit-mode strict` so runs fail on audit errors. (`autogenbook/audit/types.py:AuditSeverity`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)

## Recipes

### Resume a partial book run

1. Run once to create `structure_graph.json` and `sections/*.tex`.
2. Re-run with `--resume` to skip existing sections. (`book_builder.py:generate_contents`)

### Force JSON regeneration from TXT

Use `--use-txt` to regenerate the structure even if `--json` exists. (`main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)

### Rebuild a KB cache

Use `--rebuild-kb` to force rebuild of `.kb_cache` in the output directory. (`rag_kb.py:KnowledgeBase.build_from_directory`, `autogenbook/pipelines/book_pipeline.py:run_book`)

### Require MCP citations in proposal mode

Proposal mode enforces MCP tool availability and will error if MCP tools are unavailable. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

## Recommended use cases

- Long-form educational book drafting with consistent terminology and section continuity. (`book_builder.py:generate_contents`, `autogenbook/memory/context_memory.py:ContextMemory`)
- Research paper drafts with explicit citations and optional audits. (`autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/audit/latex_auditor.py:audit_latex`)
- Slide deck generation with optional Beamer export and narration. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
- Experimental pipelines that combine results and writing in a single run. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`, `autogenbook/templates/toy_classification/run_experiment.py:main`)

## Not recommended / limitations

- Real-time UI editing or live collaboration: only CLI workflows exist. (`main.py:parse_args`)
- Running without OpenRouter credentials or with MCP disabled in proposal mode. (`openrouter_llm.py:OpenRouterLLM.__init__`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- Expecting a built-in server or REST API: no server modules are present in the codebase. (`main.py:parse_args`, `autogenbook/orchestrator.py:run`)

## FAQ

**Where do outputs go?**
All artifacts are written under `--out-dir`, including `structure_graph.json`, section files, and logs. (`autogenbook/state.py:RunContext`, `book_builder.py:generate_contents`, `autogenbook/logging.py:get_logger`)

**Why does proposal mode fail without web tools?**
Proposal mode requires MCP paper tools when `--enable-web-rag` is set and errors if they are unavailable. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever`)

**How do I disable interactive prompts?**
Set `AUTOGENBOOK_NONINTERACTIVE=1` and optionally `AUTOGENBOOK_ASSUME_YES=1` to skip prompts. (`autogenbook/pipelines/book_pipeline.py:_ask_choice`, `autogenbook/pipelines/book_pipeline.py:_ask_yes_no`)

**Where can I see what the LLM produced?**
Agent inputs/outputs are stored in `out_dir/agent_logs` and usage is appended to `llm_usage.jsonl`. (`autogenbook/agents/io_log.py:write_agent_io`, `autogenbook/llm_usage.py:log_usage`)
