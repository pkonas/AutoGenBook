# Proposal mode repo analysis

## 1) CLI entrypoint and mode dispatch
- `main.py`:
  - `parse_args` uses `argparse` with `--mode` choices and common flags like `--input`, `--json`, `--out-dir`, `--kb-dir`, `--resume`.
  - `main` adjusts `args.json_path` for paper mode, sets `AUTOGENBOOK_FAIL_FAST_SCHEMA`, creates a `RunContext`, and calls `autogenbook.orchestrator.run`.
- `autogenbook/orchestrator.py`:
  - `run(args)` reads `args.mode` and dispatches to `run_book`, `run_paper`, or `run_scientist`.

## 2) Mode implementations

### Book mode
- Entry: `autogenbook/pipelines/book_pipeline.py:run_book`.
- Structure + writing pipeline:
  - Book JSON: `book_builder.generate_book_json_from_txt` (prompt in `prompts.py:BOOK_JSON_FROM_TXT`).
  - Graph: `book_builder.build_graph_from_book_json`, `book_builder.subdivide_graph`.
  - Section writing: `book_builder.generate_contents` (uses `BookSectionWriterAgent`, optional reviewer/revision, and context memory).
  - Partial outputs: `RunContext.structure_graph_path` (`structure_graph.json`), `sections/*.tex`, and `agent_logs/` via `autogenbook/agents/io_log.py`.
- Output conversion:
  - LaTeX assembly: `book_builder.build_latex_document`.
  - PDF: `book_builder.compile_pdf`.
  - Markdown: `book_builder.export_markdown` (LaTeX -> Markdown).
- Citations/ISO 690:
  - `book_builder._apply_iso690_citations_v2/_v3` and `autogenbook/retrieval/kb_citations`.

### Paper mode
- Entry: `autogenbook/pipelines/paper_pipeline.py:run_paper`.
- Structure + writing pipeline:
  - JSON: `_build_paper_json` (prompt in `autogenbook/prompts/paper_prompts.py:PAPER_JSON_FROM_TXT`).
  - Graph: `_build_graph_from_paper_json`, `_subdivide_paper_graph`, `save_graph_json`.
  - Section writing: `PaperSectionWriterAgent` with prompts in `autogenbook/prompts/agent_prompts.py:PAPER_SECTION_WRITER_*`.
  - Partial outputs: `structure_graph.json`, per-section `sections/*.tex`, `related_work.json`, `agent_logs/`.
- Output conversion:
  - LaTeX assembly: `_build_paper_latex`.
  - PDF/Markdown: `book_builder.compile_pdf` and `book_builder.export_markdown`.
- Citations:
  - `autogenbook/citations.extract.extract_citations` and `autogenbook/citations.ledger.CitationLedger` for BibTeX.

### Scientist mode
- Entry: `autogenbook/pipelines/scientist_pipeline.py:run_scientist`.
- Agents: `IdeaAgent`, `LiteratureAgent`, `PlanAgent`, `AnalyzeAgent`, `ReviewAgent`, `RevisionAgent`, `CodePatchAgent`, `PaperSectionWriterAgent`.
- Experiments + patching:
  - Template runs via `_prepare_run_dir` and `_run_experiment`.
  - Optional patches applied with `autogenbook/runner/patch_apply.py:apply_unified_diff`.
- Output conversion:
  - LaTeX assembly: `_build_paper_latex`.
  - PDF: `book_builder.compile_pdf`.
- Artifacts: `experiments/<run_id>/`, `review.json`, `audit_report.json`.

### Shared long-form utilities
- Graph utilities: `autogenbook/graph/doc_graph.py` (`leaf_nodes_in_order`, `attach_content_path`, `save_graph_json`, `load_graph_json`).
- Outline helper: `utils.generate_outline_text`.
- Section length enforcement: `autogenbook/length_control.enforce_section_length`.
- Context memory: `autogenbook/memory/context_memory.ContextMemory` (used in `book_builder.generate_contents`).
- Logging: `autogenbook/agents/io_log.write_agent_io` -> `out_dir/agent_logs`.

## 3) LLM instantiation/config
- Client: `openrouter_llm.OpenRouterLLM` with `openrouter_llm.LLMConfig`.
- Env/config:
  - Required for OpenRouter: `OPENROUTER_API_KEY` (local endpoints can use `AUTOGENBOOK_LLM_BASE_URL`). 
  - Optional cost tracking: `OPENROUTER_INPUT_COST_PER_M`, `OPENROUTER_OUTPUT_COST_PER_M`.
  - Optional headers: `OPENROUTER_HTTP_REFERER`, `OPENROUTER_X_TITLE`.
- Model selection patterns:
  - Default model: `openai/gpt-5-mini` in `LLMConfig`.
  - Book redundancy check uses `LLMConfig(model="openai/gpt-5-pro")` in `book_pipeline._revise_book_json_for_redundancy`.
- Message format + tool calling:
  - `OpenRouterLLM.chat` expects OpenAI-style `{role, content}` messages.
  - `BaseAgent.run` uses `chat_json_object` / `chat_json_array` with `allow_tools=False`.

## 4) MCP tools integration
- Gateway: `mcp_gateway.py` (`get_mcp_gateway`, `report_mcp_status`, `get_openai_tools`, `call_tool`, `format_tool_result`).
- Tool exposure: `openrouter_llm.OpenRouterLLM._get_mcp_tools` + `_inject_mcp_system_prompt`.
- Tool usage in retrieval: `autogenbook/retrieval/mcp_papers.py`.
  - Tool names referenced: `search_papers`, `search_arxiv`, `search_semantic`, `search_pubmed`, `search_crossref`, `search_google_scholar`, `search_iacr`, `search_biorxiv`, `search_medrxiv`.
  - `MCPPaperRetriever.retrieve` calls `mcp_gateway.call_tool(name, args)` and normalizes results to `RetrievalItem`.

## 5) RAG / Knowledge Base
- Build: `rag_kb.KnowledgeBase.build_from_directory(dir_path, cache_dir, force_rebuild)`.
- Supported formats: `.pdf`, `.docx`, `.pptx`, `.md`, `.txt` (`rag_kb.SUPPORTED_EXTS`).
- Retrieval interfaces:
  - `KnowledgeBase.retrieve(query, k)` -> `List[(Chunk, score)]`.
  - `autogenbook/retrieval/manager.RetrievalManager.retrieve(query, k, allow_web)` -> `List[RetrievalItem]`.
- Citation/ID representation:
  - KB chunks include `rid` (`RID:kb:...`) and `cite_key`.
  - `autogenbook/retrieval/kb_citations.build_kb_index` writes `kb_sources.json` for cite/rid lookup.
  - `autogenbook/citations.extract.extract_citations` parses `\cite{}` and `RID:` from LaTeX.
  - ISO 690 logic is in `book_builder._apply_iso690_citations_v2/_v3` (note the final alias `_apply_iso690_citations` is reassigned to `_apply_bibtex_citations` at end of file).

## 6) Reusable components for proposal mode
- LLM + logging: `openrouter_llm.OpenRouterLLM`, `autogenbook/agents/base.AgentContext`, `autogenbook/agents/io_log.write_agent_io`.
- KB/RAG: `rag_kb.KnowledgeBase`, `autogenbook/retrieval/manager.RetrievalManager`, `autogenbook/retrieval/kb_citations.build_kb_index`.
- Graph/outline: `autogenbook/graph/doc_graph.py`, `utils.generate_outline_text`.
- Section writing loops: `book_builder.generate_contents`, `paper_pipeline.run_paper` (per-section loop with retrieval + revisions).
- Conversion: `book_builder.compile_pdf`, `book_builder.export_markdown`, `paper_pipeline._build_paper_latex`.
- Citations: `autogenbook/citations.extract.extract_citations`, `autogenbook/citations.ledger.CitationLedger`, `book_builder._apply_iso690_citations_v3`.
