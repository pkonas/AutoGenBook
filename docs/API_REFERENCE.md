# API Reference (CLI)

## CLI entrypoint

Run the CLI via `python main.py ...`. (`main.py:main`)

Modes are dispatched by the orchestrator. (`autogenbook/orchestrator.py:run`)

## Options

All flags below are defined in `main.py:parse_args` unless noted otherwise. (`main.py:parse_args`)

| Flag | Modes | Description | Source |
| --- | --- | --- | --- |
| `--mode {book,paper,presentation,scientist,proposal,reviewer}` | All | Selects the pipeline to run. | `main.py:parse_args`, `autogenbook/orchestrator.py:run` |
| `--input`, `-i` | Book, Paper, Presentation | TXT spec input file. | `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--presentation-input` | Presentation | Presentation input file. | `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--proposal-input` | Proposal | Proposal input file. | `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `--json`, `-j` | Book, Paper, Presentation | Structure JSON path (default `book_structure.json`; paper/presentation modes swap to `paper_structure.json`/`presentation_structure.json` when default is used). | `main.py:parse_args`, `main.py:main` |
| `--use-json` | Book, Paper, Presentation | If JSON exists, use it without prompting. | `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--use-txt` | Book, Paper, Presentation | If JSON exists, regenerate from TXT without prompting. | `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--out-dir`, `-o` | All | Output directory for artifacts. | `main.py:parse_args`, `autogenbook/state.py:RunContext` |
| `--llm-base-url` | All | Override OpenAI-compatible base URL for all LLM calls in the run. | `main.py:parse_args`, `openrouter_llm.py:OpenRouterLLM.__init__` |
| `--resume` | Book, Paper, Presentation, Proposal | Resume from existing outputs in `--out-dir`. | `main.py:parse_args`, `book_builder.py:generate_contents`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `--export-tex` | Book | Export TeX/PDF from Markdown output (pandoc required). | `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book` |
| `--legacy-tex` | Book | Use legacy LLM LaTeX generation instead of Markdown-first. | `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book` |
| `--no-tex` | Book, Reviewer | Disable LaTeX output (book) or pandoc conversion to TeX (reviewer). | `main.py:parse_args`, `book_builder.py:run_book` (via `AppConfig`), `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |
| `--no-pdf` | Book, Paper, Presentation, Scientist, Reviewer | Disable PDF output/compilation. | `main.py:parse_args`, `book_builder.py:run_book` (via `AppConfig`), `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |
| `--no-md` | Book, Paper, Scientist | Disable Markdown export (presentation ignores this flag). | `main.py:parse_args`, `book_builder.py:run_book` (via `AppConfig`), `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist` |
| `--kb-dir` | Book, Paper, Presentation, Scientist | Local KB directory for RAG. | `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist` |
| `--kb1-dir` | Proposal, Reviewer | KB1 directory (requirements/norms). | `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |
| `--kb2-dir` | Proposal, Reviewer | KB2 directory (project background/thesis). | `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |
| `--rebuild-kb` | All modes with KB | Force rebuild of local KB cache. | `main.py:parse_args`, `rag_kb.py:KnowledgeBase.build_from_directory` |
| `--enable-web-rag` | Book, Paper, Presentation, Scientist, Proposal | Enable MCP/Tavily web retrieval (proposal requires MCP tools). | `main.py:parse_args`, `autogenbook/retrieval/manager.py:RetrievalManager`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `--web-rag-k` | Book, Paper, Presentation, Scientist, Proposal | Number of web retrieval results. | `main.py:parse_args`, `autogenbook/retrieval/manager.py:RetrievalManager` |
| `--paper-venue` | Paper | Target venue label for prompts. | `main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:_build_paper_json` |
| `--citation-style {bibtex,footnote}` | Paper | Choose citation rendering mode. | `main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:run_paper` |
| `--presentation-tex` | Presentation | Convert presentation Markdown to Beamer `.tex`/`.pdf`. | `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--presentation-pptx` | Presentation | Export presentation Markdown to `.pptx`. | `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`, `autogenbook/presentation_export.py:md_to_pptx` |
| `--presentation-narration` | Presentation | Generate per-slide narration text. | `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--presentation-narration-model` | Presentation | Model override for narration generation. | `main.py:parse_args` |
| `--presentation-tts` | Presentation | Synthesize audio from narration text. | `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--presentation-tts-mode {local,openrouter}` | Presentation | Select TTS backend. | `main.py:parse_args` |
| `--presentation-tts-model` | Presentation | OpenRouter TTS model override; defaults to `openai/gpt-4o-mini-tts-2025-12-15`. | `main.py:parse_args` |
| `--presentation-video` | Presentation | Render video from PDF slides and narration audio. | `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--presentation-exclude-slides` | Presentation | Exclude slide indices or ranges from audio/video (e.g., `2,5,10-12`). | `main.py:parse_args` |
| `--presentation-citations` | Presentation | Include Harvard-style citations from MCP paper tools in slide text. | `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--presentation-image-model` | Presentation | OpenRouter image model override for slide images; defaults to `openai/gpt-5.4-image-2`. | `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation` |
| `--max-iters` | Proposal, Scientist | Maximum iterations for proposal outline/review loops or scientist iterations. | `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist` |
| `--section-retries` | Proposal | Retry attempts per proposal section. | `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `--min-section-citations` | Proposal | Minimum citations per proposal section. | `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `--dont-ask` / `--dont_ask` | Proposal | Disable user prompts for missing info. | `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `--proposal-llm1-model` .. `--proposal-llm5-model` | Proposal | Per-role model overrides for proposal pipeline. | `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:_resolve_model_override` |
| `--proposal-llm1-base-url` .. `--proposal-llm5-base-url` | Proposal | Per-role base URL overrides for proposal pipeline. | `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `--reviewer-llm1-model`, `--reviewer-llm2-model` | Reviewer | Model overrides for reviewer mode. | `main.py:parse_args`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |
| `--reviewer-llm1-base-url`, `--reviewer-llm2-base-url` | Reviewer | Per-role base URL overrides for reviewer mode. | `main.py:parse_args`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |
| `--reviewer-direct-pdf` | Reviewer | Include direct PDF text from KB2 in LLM2 prompt. | `main.py:parse_args`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |
| `--audit` | Paper, Scientist, Proposal | Enable audit (default on for paper/scientist, off for proposal). | `main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `--audit-mode {off,warn,strict}` | Paper, Scientist, Proposal, Book | Audit severity. | `main.py:parse_args`, `autogenbook/audit/latex_auditor.py:audit_latex` |
| `--audit-window-chars` | Paper, Scientist, Proposal | Evidence window size for numeric claims. | `main.py:parse_args`, `autogenbook/audit/latex_auditor.py:audit_latex` |
| `--audit-book` | Book | Enable audit in book mode. | `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book` |
| `--audit-book-mode {off,warn,strict}` | Book | Audit severity for book mode. | `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book` |
| `--fail-fast-schema` | All | Stop on schema validation errors (no repair). | `main.py:parse_args`, `autogenbook/agents/base.py:BaseAgent._validate_with_repair` |

### Notes

- Book mode is Markdown-first; `--export-tex` enables TeX/PDF export from Markdown. `--legacy-tex` switches back to the LLM LaTeX pipeline. (`autogenbook/pipelines/book_pipeline.py:run_book`)
- `--no-tex` is enforced in book mode (via `AppConfig`) and reviewer mode (pandoc conversion), but paper/scientist pipelines always write `.tex` outputs. (`book_builder.py:AppConfig`, `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`)

## Exit codes

- `0`: success. (`autogenbook/pipelines/*:run_*`)
- `2`: missing input/KB or invalid prerequisites. (`autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)
- `4`: audit failure in strict mode. (`autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

## No HTTP API

This repository exposes a CLI and internal Python modules; no HTTP server endpoints are defined. (`main.py:parse_args`, `autogenbook/orchestrator.py:run`)
