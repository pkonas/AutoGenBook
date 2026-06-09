# AutoGenBook (CLI)

AutoGenBook is a modular Python CLI for generating long-form documents from short specifications using mode-specific pipelines (book, paper, presentation, scientist, proposal, reviewer). Each pipeline builds a document graph, injects retrieval context, writes leaf sections with agent prompts, and assembles Markdown by default (book/paper) with optional LaTeX/PDF conversion; legacy LaTeX-first generation remains available. (`main.py:parse_args`, `autogenbook/orchestrator.py:run`, `autogenbook/pipelines/*`, `book_builder.py:build_markdown_document`, `book_builder.py:build_latex_document`, `book_builder.py:compile_pdf`)

This project exists to make long-form generation repeatable and auditable: structure is explicit (graph nodes with page budgets), sources are tracked via stable IDs, and optional audits can flag missing citations or unsupported numeric claims. (`book_builder.py:build_graph_from_book_json`, `autogenbook/graph/doc_graph.py`, `rag_kb.py:KnowledgeBase`, `autogenbook/retrieval/types.py:RetrievalItem`, `autogenbook/audit/latex_auditor.py:audit_latex`)

## Who it is for

- Authors drafting structured books or reports with reproducible section boundaries. (`book_builder.py:generate_contents`)
- Researchers drafting papers with explicit citations and related-work scaffolding. (`autogenbook/pipelines/paper_pipeline.py:run_paper`)
- Speakers drafting slide decks with grounded, concise talking points. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
- Teams running an experiment + writing loop from the same run artifacts. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`)
- Grant proposal and review workflows that need MCP-tool citations and traceability. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)

## Modes at a glance

| Mode | Primary input | Core steps | Key outputs |
| --- | --- | --- | --- |
| **book** | TXT spec (`--input`) or JSON (`--json`) | TXT -> JSON -> graph -> sections -> Markdown (default) -> optional LaTeX/PDF (pandoc) | `structure_graph.json`, `sections/*.md`, `<title>.md`, optional `.tex`/`.pdf` | (`autogenbook/pipelines/book_pipeline.py:run_book`, `book_builder.py:build_markdown_document`, `book_builder.py:build_latex_document`)
| **paper** | TXT spec (`--input`) or JSON (`--json`) | TXT -> JSON -> graph -> sections -> Markdown (default) -> LaTeX/PDF (pandoc) | `structure_graph.json`, `sections/*.md`, `related_work.json`, `<title>.md`, optional `.tex`/`.pdf`, `refs.bib` (BibTeX) | (`autogenbook/pipelines/paper_pipeline.py:run_paper`)
| **presentation** | TXT spec (`--input`) or JSON (`--json`) | TXT -> JSON -> graph -> slides -> Markdown (default) -> optional PPTX / Beamer LaTeX/PDF + narration/audio/video | `structure_graph.json`, `slides/*.md`, `images/*.png`, `<title>.md`, optional `.pptx` / `.tex` / `.pdf`, narration `.md/.json`, audio `.wav`, video `.mp4` | (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
| **scientist** | Template + optional KB | Idea -> literature -> plan -> experiment -> analysis -> write -> review -> revise | `experiments/<run_id>/metrics.json`, `review.json`, `audit_report.json`, LaTeX outputs | (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`, `autogenbook/templates/toy_classification/run_experiment.py:main`)
| **proposal** | `proposal_input.txt` + KB1/KB2 | Requirements -> outline -> sections w/ MCP citations -> final review | `outline/`, `draft/`, `final/`, per-section artifacts, `proposal_final.md` | (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
| **reviewer** | KB2 (+ optional KB1) | Compose reviewer prompt -> draft review | `review_final.md` (+ optional PDF/TeX via pandoc) | (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)

## How it works (core flow)

1. Parse CLI args and create a run context (output paths + run id). (`main.py:parse_args`, `autogenbook/state.py:RunContext`)
2. Load the prompt pack for the selected mode. (`autogenbook/prompts/*_loader.py`, `autogenbook/prompts/registry.py:set_prompt_registry`)
3. Build or load a structure graph (book/paper/presentation). (`book_builder.py:generate_book_json_from_txt`, `book_builder.py:build_graph_from_book_json`, `autogenbook/pipelines/paper_pipeline.py:_build_graph_from_paper_json`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
4. Subdivide oversized nodes to leaf sections. (`book_builder.py:subdivide_graph`, `autogenbook/pipelines/paper_pipeline.py:_subdivide_paper_graph`)
5. Generate leaf section content with retrieval context and agent prompts. (`book_builder.py:generate_contents`, `autogenbook/agents/base.py:BaseAgent`, `autogenbook/retrieval/manager.py:RetrievalManager`)
6. Assemble Markdown (book/paper default) or LaTeX (legacy), then optionally convert via pandoc and compile PDF. (`book_builder.py:build_markdown_document`, `book_builder.py:build_latex_document`, `book_builder.py:compile_pdf`)

## Key features

- Document graph planning with explicit section budgets. (`book_builder.py:build_graph_from_book_json`, `autogenbook/graph/doc_graph.py`)
- Local RAG knowledge base built from PDF/DOCX/PPTX/MD/TXT with BM25. (`rag_kb.py:KnowledgeBase`, `rag_kb.py:SUPPORTED_EXTS`)
- Optional web retrieval via MCP paper tools and/or Tavily search. (`autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever`, `autogenbook/retrieval/tavily.py:TavilyRetriever`)
- Agent outputs validated against Pydantic schemas with optional repair. (`autogenbook/agents/base.py:BaseAgent`, `autogenbook/schemas/*`)
- LaTeX auditing for unknown citations, missing figures, and numeric evidence gaps. (`autogenbook/audit/latex_auditor.py:audit_latex`)
- Resumable runs via `structure_graph.json` and per-section files. (`autogenbook/graph/doc_graph.py:save_graph_json`, `book_builder.py:generate_contents`)

## Requirements

- Python dependencies are declared in `requirements.txt`. (`requirements.txt`)
- OpenRouter API key is required when using OpenRouter; OpenAI-compatible endpoints can be set via `--llm-base-url` or `AUTOGENBOOK_LLM_BASE_URL`. (`openrouter_llm.py:OpenRouterLLM.__init__`, `main.py:parse_args`)
- PDF output requires LuaLaTeX (`lualatex`). (`book_builder.py:compile_pdf`)
- Book/paper Markdown→TeX/PDF conversion uses pandoc. Proposal/reviewer conversions also use pandoc when available. (`book_builder.py:_convert_markdown_to_latex`, `autogenbook/pipelines/proposal_pipeline.py:_convert_with_pandoc`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)
- The scientist template uses scikit-learn and matplotlib; fallback PNG is written if matplotlib is unavailable. (`autogenbook/templates/toy_classification/run_experiment.py:_run_sklearn`, `_plot`)

## Install

```bash
pip install -r requirements.txt
```
Source: `requirements.txt`

## Quickstart

Set the OpenRouter key:

```bash
export OPENROUTER_API_KEY="YOUR_KEY"
```
Source: `openrouter_llm.py:OpenRouterLLM.__init__`

Use a local OpenAI-compatible server (e.g., LM Studio):

```bash
export AUTOGENBOOK_LLM_BASE_URL="http://localhost:1234/v1"
# Optional if your server requires a key:
export AUTOGENBOOK_LLM_API_KEY="local-key"
```
Source: `openrouter_llm.py:OpenRouterLLM.__init__`

Book mode (sample input; Markdown-first):

```bash
python main.py --mode book --input input/book/book_input.txt --out-dir output/book/out_book
```
Source: `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`

Book mode now emits Markdown by default; add `--export-tex` to produce TeX/PDF via pandoc. (`autogenbook/pipelines/book_pipeline.py:run_book`)

Paper mode (sample input; Markdown-first):

```bash
python main.py --mode paper --input input/paper/paper_input.txt --out-dir output/paper/out_paper
```
Source: `main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:run_paper`

Paper mode now emits Markdown by default and converts to TeX/PDF via pandoc unless disabled. Use `--legacy-tex` to force the old LaTeX-first pipeline. (`autogenbook/pipelines/paper_pipeline.py:run_paper`)

Disable PDF if LuaLaTeX is unavailable:

```bash
python main.py --mode book --input input/book/book_input.txt --out-dir output/book/out_book --no-pdf
```
Source: `book_builder.py:compile_pdf`, `main.py:parse_args`

More examples: `docs/EXAMPLES.md`. (`docs/EXAMPLES.md`)

## CLI reference (complete)

All flags are defined in `main.py:parse_args`. (`main.py:parse_args`)

### Core

- `--mode {book,paper,presentation,scientist,proposal,reviewer}`: Select pipeline. (`main.py:parse_args`, `autogenbook/orchestrator.py:run`)
- `--out-dir`, `-o`: Output directory. (`main.py:parse_args`, `autogenbook/state.py:RunContext`)
- `--llm-base-url`: Override OpenAI-compatible base URL for all LLM calls in the run. (`main.py:parse_args`, `openrouter_llm.py:OpenRouterLLM.__init__`)

### Inputs and structure

- `--input`, `-i`: TXT spec (book/paper/presentation). (`main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
- `--presentation-input`: Presentation input file. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
- `--proposal-input`: Proposal input file. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- `--json`, `-j`: Structure JSON (book/paper/presentation). (`main.py:parse_args`, `main.py:main`)
- `--use-json`: Use JSON without prompting. (`main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)
- `--use-txt`: Regenerate JSON from TXT. (`main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)
- `--resume`: Resume from previous outputs. (`main.py:parse_args`, `book_builder.py:generate_contents`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

### Retrieval / KB

- `--kb-dir`: Local KB for book/paper/scientist. (`main.py:parse_args`, `rag_kb.py:KnowledgeBase.build_from_directory`)
- `--kb1-dir`, `--kb2-dir`: Proposal/reviewer KBs. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)
- `--rebuild-kb`: Force KB rebuild. (`main.py:parse_args`, `rag_kb.py:KnowledgeBase.build_from_directory`)
- `--enable-web-rag`: Enable web retrieval (proposal requires MCP tools). (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- `--web-rag-k`: Number of web results. (`main.py:parse_args`, `autogenbook/retrieval/manager.py:RetrievalManager`)

### Paper

- `--paper-venue`: Target venue string. (`main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:_build_paper_json`)
- `--citation-style {bibtex,footnote}`: Paper citation mode. (`main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)

### Proposal

- `--max-iters`: Max iterations (proposal outline/review loop; scientist iterations). (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`)
- `--section-retries`: Retry attempts per proposal section. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- `--min-section-citations`: Minimum citations per section. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- `--dont-ask` / `--dont_ask`: Disable user prompts. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- `--proposal-llm1-model` .. `--proposal-llm5-model`: Per-role model overrides. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:_resolve_model_override`)
- `--proposal-llm1-base-url` .. `--proposal-llm5-base-url`: Per-role base URL overrides. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

### Reviewer

- `--reviewer-llm1-model`, `--reviewer-llm2-model`: Model overrides. (`main.py:parse_args`, `autogenbook/pipelines/reviewer_pipeline.py:_resolve_model_override`)
- `--reviewer-llm1-base-url`, `--reviewer-llm2-base-url`: Per-role base URL overrides. (`main.py:parse_args`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)
- `--reviewer-direct-pdf`: Include direct PDF text from KB2 in prompt. (`main.py:parse_args`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)

### Output toggles

- `--no-tex`: Suppress `.tex` as a final artifact for book/paper/scientist (LaTeX still runs when PDF is requested; paper still generates intermediate `.tex` for PDF). Disable pandoc TeX conversion in reviewer. (`main.py:parse_args`, `book_builder.py:AppConfig`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)
- `--no-pdf`: Disable PDF output. (`main.py:parse_args`, `book_builder.py:AppConfig`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)
- `--no-md`: Disable Markdown export (book/paper/scientist). (`main.py:parse_args`, `book_builder.py:AppConfig`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`)

### Auditing and schema validation

- `--audit`: Enable audit (paper/scientist default on; proposal default off). (`main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- `--audit-mode {off,warn,strict}`: Audit severity. (`main.py:parse_args`, `autogenbook/audit/latex_auditor.py:audit_latex`)
- `--audit-window-chars`: Evidence window size for numeric claims. (`main.py:parse_args`, `autogenbook/audit/latex_auditor.py:audit_latex`)
- Strict audit stops the run before PDF emission if errors are found. (`autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/book_pipeline.py:run_book`)
- `--audit-book`: Enable audit in book mode. (`main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`)
- `--audit-book-mode {off,warn,strict}`: Book audit severity. (`main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`)
- `--fail-fast-schema`: Fail immediately on schema validation errors. (`main.py:parse_args`, `autogenbook/agents/base.py:BaseAgent._validate_with_repair`)

Complete reference: `docs/API_REFERENCE.md`. (`docs/API_REFERENCE.md`)

## Mode-specific arguments and examples

This section groups flags by mode and shows practical command lines. All flags are defined in `main.py:parse_args`. (`main.py:parse_args`)

Sample specs in this repo live under `input/` (e.g., `input/book/book_input.txt`, `input/paper/paper_input.txt`, `input/presentation/presentation_input.txt`, `input/proposal/proposal_input.txt`), so most runs pass explicit `--input` or `--proposal-input`. (`input/book/book_input.txt`, `input/paper/paper_input.txt`, `input/presentation/presentation_input.txt`, `input/proposal/proposal_input.txt`)

### Book mode (`--mode book`)

Arguments and defaults:

| Flag | Required? | Default | Notes |
| --- | --- | --- | --- |
| `--input`, `-i` | Yes (unless using `--use-json`) | `book_input.txt` | TXT spec used to build the book JSON. (`main.py:parse_args`, `book_builder.py:generate_book_json_from_txt`) |
| `--json`, `-j` | Optional | `book_structure.json` | Structure JSON; used when `--use-json` is set. (`main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`) |
| `--use-json` / `--use-txt` | Optional | n/a | Mutually exclusive; force JSON reuse or regeneration. (`main.py:parse_args`) |
| `--kb-dir` | Optional | none | Build local KB from PDF/DOCX/PPTX/MD/TXT. (`rag_kb.py:KnowledgeBase.build_from_directory`) |
| `--rebuild-kb` | Optional | false | Force KB rebuild when `--kb-dir` is set. (`rag_kb.py:KnowledgeBase.build_from_directory`) |
| `--enable-web-rag`, `--web-rag-k` | Optional | `false` / `5` | Enable web retrieval (MCP/Tavily) and set result count; warns and falls back to KB if tools are unavailable. (`autogenbook/pipelines/book_pipeline.py:run_book`) |
| `--resume` | Optional | false | Skip already-generated sections (`sections/*.md` by default; legacy LaTeX uses `.tex`). (`book_builder.py:generate_contents`) |
| `--export-tex` | Optional | false | Book mode: export TeX/PDF from Markdown output (pandoc). (`main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`) |
| `--legacy-tex` | Optional | false | Book mode: use legacy LLM LaTeX generation instead of Markdown-first. (`main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`) |
| `--audit-book`, `--audit-book-mode` | Optional | `false` / `warn` | Enable book audit and set severity. (`autogenbook/pipelines/book_pipeline.py:run_book`) |
| `--no-tex`, `--no-pdf`, `--no-md` | Optional | false | Disable LaTeX/PDF/Markdown outputs. (`book_builder.py:AppConfig`) |
| `--out-dir`, `-o` | Optional | `./out` | Output directory root for all artifacts. (`main.py:parse_args`, `autogenbook/state.py:RunContext`) |

Book mode now generates Markdown sections by default and only exports TeX/PDF when `--export-tex` is set (requires `pandoc`). Use `--legacy-tex` to force the older LaTeX-first pipeline if conversion fails. (`autogenbook/pipelines/book_pipeline.py:run_book`)

Example: book with KB rebuild, web RAG, and resume:

```bash
python main.py --mode book --input input/book/book_input.txt --kb-dir input/book/kb --rebuild-kb --enable-web-rag --web-rag-k 6 --resume --out-dir output/book/out_book
```
Source: `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`

Example: use a prebuilt structure JSON (skip TXT conversion):

```bash
python main.py --mode book --json book_structure.json --use-json --out-dir output/book/out_json
```
Source: `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`

Example: force TXT regeneration + strict audit without PDF:

```bash
python main.py --mode book --input input/book/book_input.txt --use-txt --audit-book --audit-book-mode strict --no-pdf --out-dir output/book/out_rebuild
```
Source: `main.py:parse_args`, `autogenbook/pipelines/book_pipeline.py:run_book`

### Paper mode (`--mode paper`)

Arguments and defaults:

| Flag | Required? | Default | Notes |
| --- | --- | --- | --- |
| `--input`, `-i` | Yes (unless using `--use-json`) | `book_input.txt` | TXT spec used to build the paper JSON. (`main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:_build_paper_json`) |
| `--json`, `-j` | Optional | `paper_structure.json` | Default JSON switches to `paper_structure.json` in paper mode. (`main.py:parse_args`, `main.py:main`) |
| `--use-json` / `--use-txt` | Optional | n/a | Mutually exclusive; force JSON reuse or regeneration. (`main.py:parse_args`) |
| `--paper-venue` | Optional | `arXiv` | Venue label used in prompts. (`main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:_build_paper_json`) |
| `--citation-style` | Optional | `bibtex` | `bibtex` or `footnote`. (`main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:run_paper`) |
| `--audit`, `--audit-mode`, `--audit-window-chars` | Optional | on / `warn` / `600` | Audit defaults to enabled; `--audit-mode off` disables it. (`autogenbook/pipelines/paper_pipeline.py:run_paper`) |
| `--kb-dir`, `--rebuild-kb` | Optional | none | Build local KB for retrieval. (`rag_kb.py:KnowledgeBase.build_from_directory`) |
| `--enable-web-rag`, `--web-rag-k` | Optional | `false` / `5` | Enable web retrieval (MCP/Tavily); warns and falls back to KB if tools are unavailable. (`autogenbook/pipelines/paper_pipeline.py:run_paper`) |
| `--legacy-tex` | Optional | false | Use legacy LaTeX-first generation instead of Markdown-first. (`autogenbook/pipelines/paper_pipeline.py:run_paper`) |
| `--no-pdf`, `--no-md` | Optional | false | Disable PDF/Markdown outputs. (`autogenbook/pipelines/paper_pipeline.py:run_paper`) |
| `--out-dir`, `-o` | Optional | `./out` | Output directory root for all artifacts. (`main.py:parse_args`, `autogenbook/state.py:RunContext`) |

Example: paper with custom venue, footnote citations, and strict audit:

```bash
python main.py --mode paper --input input/paper/paper_input.txt --paper-venue "ACL 2025" --citation-style footnote --audit --audit-mode strict --out-dir output/paper/out_paper
```
Source: `main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:run_paper`

Example: paper with web RAG enabled:

```bash
python main.py --mode paper --input input/paper/paper_input.txt --enable-web-rag --web-rag-k 8 --out-dir output/paper/out_web
```
Source: `main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:run_paper`

Example: reuse an existing paper structure JSON:

```bash
python main.py --mode paper --json paper_structure.json --use-json --out-dir output/paper/out_json
```
Source: `main.py:parse_args`, `autogenbook/pipelines/paper_pipeline.py:run_paper`

### Presentation mode (`--mode presentation`)

Arguments and defaults:

| Flag | Required? | Default | Notes |
| --- | --- | --- | --- |
| `--input`, `-i` | Yes (unless using `--use-json`) | `presentation_input.txt` | TXT spec used to build the presentation JSON. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--json`, `-j` | Optional | `presentation_structure.json` | Default JSON switches to `presentation_structure.json` in presentation mode. (`main.py:parse_args`, `main.py:main`) |
| `--presentation-tex` | Optional | false | Convert Markdown to Beamer `.tex`/`.pdf`. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--presentation-pptx` | Optional | false | Export Markdown deck to PowerPoint `.pptx`. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--presentation-narration` | Optional | false | Generate per-slide narration text. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--presentation-narration-model` | Optional | none | Model override for narration. (`main.py:parse_args`) |
| `--presentation-tts` | Optional | false | Generate audio from narration. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--presentation-tts-mode` | Optional | `openrouter` | `openrouter` or `local` TTS backend. (`main.py:parse_args`) |
| `--presentation-tts-model` | Optional | none | OpenRouter TTS model override. Default: `openai/gpt-4o-mini-tts-2025-12-15`. (`main.py:parse_args`) |
| `--presentation-video` | Optional | false | Render video from PDF slides + audio. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--presentation-exclude-slides` | Optional | empty | Exclude slide indices or ranges from audio/video (e.g., `2,5,10-12`). (`main.py:parse_args`) |
| `--presentation-citations` | Optional | false | Include Harvard-style citations from MCP paper tools in slide text. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--presentation-image-model` | Optional | none | OpenRouter image model override for slide images. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--no-image` | Optional | false | Disable slide image generation entirely. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--disable-general-knowledge-citation` | Optional | false | Forbid/remove `General background knowledge` marker from slide text. (`main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--kb-dir`, `--rebuild-kb` | Optional | none | Build local KB for retrieval. (`rag_kb.py:KnowledgeBase.build_from_directory`) |
| `--enable-web-rag`, `--web-rag-k` | Optional | `false` / `5` | Enable web retrieval (MCP/Tavily); warns and falls back to KB if tools are unavailable. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--resume` | Optional | false | Skip already-generated slides. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`) |
| `--out-dir`, `-o` | Optional | `./out` | Output directory root for all artifacts. (`main.py:parse_args`, `autogenbook/state.py:RunContext`) |

Example: basic presentation with web RAG:

```bash
python main.py --mode presentation --input input/presentation/presentation_input.txt --enable-web-rag --web-rag-k 6 --out-dir output/presentation/out_presentation --disable-general-knowledge-citation --no-image
```
Source: `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`

Example: Beamer export + narration + audio:

```bash
python main.py --mode presentation --input input/presentation/presentation_input.txt --presentation-pptx --presentation-tex --presentation-narration --presentation-tts --out-dir output/presentation/out_media
```
Source: `main.py:parse_args`, `autogenbook/pipelines/presentation_pipeline.py:run_presentation`

Note: Beamer export requires `pandoc` + `pypandoc`. Audio/video features require optional packages such as `pymupdf`, `moviepy`, `pydub`, `soundfile`, `scipy`, `TTS`, and `ffmpeg`. Slide image generation uses an OpenRouter image model (override with `--presentation-image-model`, disable with `--no-image`).

### Scientist mode (`--mode scientist`)

Arguments and defaults:

| Flag | Required? | Default | Notes |
| --- | --- | --- | --- |
| `--max-iters` | Optional | `1` | Number of patch iterations. (`autogenbook/pipelines/scientist_pipeline.py:DEFAULT_MAX_ITERS`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`) |
| `--kb-dir`, `--rebuild-kb` | Optional | none | Build local KB for retrieval. (`rag_kb.py:KnowledgeBase.build_from_directory`) |
| `--enable-web-rag`, `--web-rag-k` | Optional | `false` / `5` | Enable web retrieval (MCP/Tavily); warns and falls back to KB if tools are unavailable. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`) |
| `--audit`, `--audit-mode`, `--audit-window-chars` | Optional | on / `strict` / `600` | Audit defaults to enabled; scientist forces `strict` when `--audit-mode` is omitted. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`) |
| `--no-pdf`, `--no-md` | Optional | false | Disable PDF/Markdown outputs. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`) |
| `--out-dir`, `-o` | Optional | `./out` | Output directory root for all artifacts. (`main.py:parse_args`, `autogenbook/state.py:RunContext`) |

Example: run two scientist iterations without PDF:

```bash
python main.py --mode scientist --max-iters 2 --no-pdf --out-dir output/scientist/out_scientist
```
Source: `main.py:parse_args`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`

Example: disable audit and enable web retrieval:

```bash
python main.py --mode scientist --audit-mode off --enable-web-rag --web-rag-k 5 --out-dir output/scientist/out_web
```
Source: `main.py:parse_args`, `autogenbook/pipelines/scientist_pipeline.py:run_scientist`

### Proposal mode (`--mode proposal`)

Arguments and defaults:

| Flag | Required? | Default | Notes |
| --- | --- | --- | --- |
| `--proposal-input` | Yes | `proposal_input.txt` | Proposal spec file. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`) |
| `--kb1-dir`, `--kb2-dir` | Yes | none | Required KB directories. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`) |
| `--enable-web-rag` | Yes | `false` | Proposal mode errors without MCP paper tools. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`) |
| `--web-rag-k` | Optional | `5` | Web retrieval result count. (`autogenbook/retrieval/manager.py:RetrievalManager`) |
| `--rebuild-kb` | Optional | false | Force rebuild of KB1/KB2 caches. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`) |
| `--max-iters` | Optional | `3` | Outline/review loop count (default set in `main.py`). (`main.py:main`) |
| `--section-retries` | Optional | `3` | Retry attempts per section. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`) |
| `--min-section-citations` | Optional | `1` | Minimum citations per section. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`) |
| `--dont-ask` / `--dont_ask` | Optional | false | Disable user prompts; rely on KB/web. (`main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`) |
| `--proposal-llm1-model` .. `--proposal-llm5-model` | Optional | none | Per-role model overrides. (`autogenbook/pipelines/proposal_pipeline.py:_resolve_model_override`) |
| `--proposal-llm1-base-url` .. `--proposal-llm5-base-url` | Optional | none | Per-role base URL overrides (OpenAI-compatible endpoints). (`main.py:parse_args`, `openrouter_llm.py:OpenRouterLLM.__init__`) |
| `--audit`, `--audit-mode`, `--audit-window-chars` | Optional | off / `warn` / `600` | Final audit controls. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`) |
| `--out-dir`, `-o` | Optional | `./out` | Output directory root for all artifacts. (`main.py:parse_args`, `autogenbook/state.py:RunContext`) |

Example: proposal with required KBs and MCP citations:

```bash
python main.py --mode proposal --proposal-input input/proposal/proposal_input.txt --kb1-dir input/proposal/kb1 --kb2-dir input/proposal/kb2 --enable-web-rag --out-dir output/proposal/out_proposal
```
Source: `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`

Example: proposal in non-interactive mode with stricter citation requirements:

```bash
python main.py --mode proposal --proposal-input input/proposal/proposal_input.txt --kb1-dir input/proposal/kb1 --kb2-dir input/proposal/kb2 --enable-web-rag --min-section-citations 2 --dont-ask --out-dir output/proposal/out_strict
```
Source: `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`

Example: proposal with custom model overrides and loop tuning:

```bash
python main.py --mode proposal --proposal-input input/proposal/proposal_input.txt --kb1-dir input/proposal/kb1 --kb2-dir input/proposal/kb2 --enable-web-rag --max-iters 4 --section-retries 2 --proposal-llm1-model openai/gpt-5-mini --proposal-llm4-model openai/gpt-5-mini --out-dir output/proposal/out_custom
```
Source: `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `openrouter_llm.py:LLMConfig`

Example: proposal with per-role endpoints (LLM1/LLM4 on local server):

```bash
python main.py --mode proposal --proposal-input input/proposal/proposal_input.txt --kb1-dir input/proposal/kb1 --kb2-dir input/proposal/kb2 --enable-web-rag --proposal-llm1-base-url http://localhost:1234/v1 --proposal-llm4-base-url http://localhost:1234/v1 --out-dir output/proposal/out_local_roles
```
Source: `main.py:parse_args`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `openrouter_llm.py:OpenRouterLLM.__init__`

### Reviewer mode (`--mode reviewer`)

Arguments and defaults:

| Flag | Required? | Default | Notes |
| --- | --- | --- | --- |
| `--kb2-dir` | Yes | none | Required KB2 directory (thesis/work). (`autogenbook/pipelines/reviewer_pipeline.py:build_or_load_kb2`) |
| `--kb1-dir` | Optional | none | Optional KB1 directory (norms/requirements). (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`) |
| `--rebuild-kb` | Optional | false | Force rebuild of reviewer KB caches. (`autogenbook/pipelines/reviewer_pipeline.py:_configure_reviewer_kb`) |
| `--reviewer-direct-pdf` | Optional | false | Include direct PDF text in the review prompt. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`) |
| `--reviewer-llm1-model`, `--reviewer-llm2-model` | Optional | none | Model overrides. (`autogenbook/pipelines/reviewer_pipeline.py:_resolve_model_override`) |
| `--reviewer-llm1-base-url`, `--reviewer-llm2-base-url` | Optional | none | Per-role base URL overrides (OpenAI-compatible endpoints). (`main.py:parse_args`, `openrouter_llm.py:OpenRouterLLM.__init__`) |
| `--no-pdf`, `--no-tex` | Optional | false | Skip pandoc conversions. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`) |
| `--out-dir`, `-o` | Optional | `./out` | Output directory root for all artifacts. (`main.py:parse_args`, `autogenbook/state.py:RunContext`) |

Example: reviewer with direct PDF context and no conversions:

```bash
python main.py --mode reviewer --kb2-dir input/reviewer/kb2 --reviewer-direct-pdf --no-pdf --no-tex --out-dir output/reviewer/out_reviewer
```
Source: `main.py:parse_args`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`

Example: reviewer with KB1 norms and model overrides:

```bash
python main.py --mode reviewer --kb2-dir input/reviewer/kb2 --kb1-dir input/reviewer/kb1 --reviewer-llm1-model openai/gpt-5-mini --reviewer-llm2-model openai/gpt-5-mini --out-dir output/reviewer/out_models
```
Source: `main.py:parse_args`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`, `openrouter_llm.py:LLMConfig`

Example: reviewer with per-role endpoint overrides:

```bash
python main.py --mode reviewer --kb2-dir input/reviewer/kb2 --reviewer-llm1-base-url http://localhost:1234/v1 --reviewer-llm2-base-url http://localhost:1234/v1 --out-dir output/reviewer/out_local_roles
```
Source: `main.py:parse_args`, `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`, `openrouter_llm.py:OpenRouterLLM.__init__`

### Cross-mode advanced flags

| Flag | Default | Notes |
| --- | --- | --- |
| `--fail-fast-schema` | false | Fail immediately on schema validation errors (also sets `AUTOGENBOOK_FAIL_FAST_SCHEMA=1`). (`main.py:main`, `autogenbook/agents/base.py:BaseAgent._validate_with_repair`) |
| `--llm-base-url` | OpenRouter base URL | Override OpenAI-compatible LLM endpoint for the run (LM Studio, local gateway, etc.). (`main.py:parse_args`, `openrouter_llm.py:OpenRouterLLM.__init__`) |

## Environment variables (complete)

### OpenRouter

| Variable | Required | Behavior | Source |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | Yes (OpenRouter) | Required for OpenRouter; optional for local endpoints. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `OPENROUTER_HTTP_REFERER` | No | Optional header. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `OPENROUTER_X_TITLE` | No | Optional header. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `OPENROUTER_INPUT_COST_PER_M` | No | Cost estimation input rate. | `openrouter_llm.py:OpenRouterLLM.__init__`, `main.py` |
| `OPENROUTER_OUTPUT_COST_PER_M` | No | Cost estimation output rate. | `openrouter_llm.py:OpenRouterLLM.__init__`, `main.py` |
| `OPENROUTER_MAX_RETRIES` | No | Retry count for transient errors (default 3). | `openrouter_llm.py:_chat_once` |
| `OPENAI_API_KEY` | No | Optional fallback API key for OpenAI-compatible servers. | `openrouter_llm.py:OpenRouterLLM.__init__` |

### AutoGenBook general

| Variable | Required | Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_LLM_BASE_URL` | No | Override OpenAI-compatible base URL for all LLM calls. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `AUTOGENBOOK_LLM_API_KEY` | No | Optional API key override for non-OpenRouter endpoints. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `AUTOGENBOOK_FORCE_MINI_MODEL` | No | Forces `openai/gpt-5-mini` for all runs. | `openrouter_llm.py:OpenRouterLLM.__init__` |
| `AUTOGENBOOK_FAIL_FAST_SCHEMA` | No | Fail immediately on schema validation. | `main.py:main`, `autogenbook/agents/base.py:BaseAgent._validate_with_repair` |
| `AUTOGENBOOK_NONINTERACTIVE` | No | Skip interactive prompts. | `autogenbook/pipelines/book_pipeline.py:_ask_choice`, `autogenbook/pipelines/proposal_pipeline.py:_is_noninteractive` |
| `AUTOGENBOOK_ASSUME_YES` | No | Auto-accept yes/no prompts (book). | `autogenbook/pipelines/book_pipeline.py:_ask_yes_no` |
| `AUTOGENBOOK_SMOKE_FAST` | No | Fast smoke mode. | `autogenbook/smoke_test.py:main` |

### Knowledge base

| Variable | Required | Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_KB_OCR` | No | Enable OCR for PDFs. | `rag_kb.py:KnowledgeBase.build_from_directory` |
| `AUTOGENBOOK_KB_OCR_LANG` | No | OCR language (default `eng`). | `rag_kb.py:KnowledgeBase.build_from_directory` |
| `AUTOGENBOOK_KB_HEADING_CHUNKS` | No | Chunk Markdown by headings. | `rag_kb.py:KnowledgeBase.build_from_directory` |

### MCP retrieval cache

| Variable | Required | Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_MCP_CACHE_DIR` | No | Cache directory for MCP tool results. | `autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever.__post_init__` |
| `AUTOGENBOOK_MCP_CACHE_TTL_S` | No | Cache TTL (seconds). | `autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever.__post_init__` |
| `AUTOGENBOOK_MCP_CACHE_MAX_FILES` | No | Cache size limit. | `autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever.__post_init__` |

### Proposal

| Variable | Required | Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_PROPOSAL_LLM1_MODEL` .. `AUTOGENBOOK_PROPOSAL_LLM5_MODEL` | No | Per-role model overrides. | `autogenbook/pipelines/proposal_pipeline.py:_resolve_model_override` |
| `AUTOGENBOOK_PROPOSAL_LLM1_BASE_URL` .. `AUTOGENBOOK_PROPOSAL_LLM5_BASE_URL` | No | Per-role base URL overrides. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_PREV_SECTIONS` | No | Number of previous sections kept for context. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_LLM1_MAX_ROUNDS` | No | Max LLM1 rounds. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_SECTION_CHUNK_CHARS` | No | Section prompt chunk size. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_SECTION_MAX_CHUNKS` | No | Max section chunks. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_REVIEW_CHUNK_CHARS` | No | Review prompt chunk size. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |
| `AUTOGENBOOK_PROPOSAL_FINAL_REVIEW_REPAIRS` | No | Final review repair attempts. | `autogenbook/pipelines/proposal_pipeline.py:run_proposal` |

### Reviewer

| Variable | Required | Behavior | Source |
| --- | --- | --- | --- |
| `AUTOGENBOOK_REVIEWER_LLM1_MODEL` | No | Reviewer LLM1 override. | `autogenbook/pipelines/reviewer_pipeline.py:_resolve_model_override` |
| `AUTOGENBOOK_REVIEWER_LLM2_MODEL` | No | Reviewer LLM2 override. | `autogenbook/pipelines/reviewer_pipeline.py:_resolve_model_override` |
| `AUTOGENBOOK_REVIEWER_LLM1_BASE_URL` | No | Reviewer LLM1 base URL override. | `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |
| `AUTOGENBOOK_REVIEWER_LLM2_BASE_URL` | No | Reviewer LLM2 base URL override. | `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer` |

### Tavily

| Variable | Required | Behavior | Source |
| --- | --- | --- | --- |
| `TAVILY_API_KEY` | No | Enables Tavily web search. | `autogenbook/retrieval/tavily.py:search_web` |

### MCP gateway

| Variable | Required | Behavior | Source |
| --- | --- | --- | --- |
| `MCP_GATEWAY_ENABLE` | No | Enable/disable MCP gateway. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_URL` | No | Base URL for gateway. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_TRANSPORT` | No | `sse` or `streaming`. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_RPC_PATH` | No | RPC path (default `/mcp`). | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_SSE_PATH` | No | SSE path (default `/sse`). | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_MESSAGE_PATH` | No | Message path (default `/message`). | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_RPC_URL` | No | Full RPC URL override. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_SSE_URL` | No | Full SSE URL override. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_MESSAGE_URL` | No | Full message URL override. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_TIMEOUT` | No | Gateway timeout seconds. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_CACHE_TTL` | No | Tool list cache TTL seconds. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_SSE_TIMEOUT` | No | SSE stream timeout. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_SSE_ENDPOINT_TIMEOUT` | No | SSE endpoint discovery timeout. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_API_KEY` | No | Gateway API key. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_API_HEADER` | No | Header for API key. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_API_PREFIX` | No | Header prefix. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_GATEWAY_PROMPT_ECHO` | No | Echo API key prompt. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_PROTOCOL_VERSION_SSE` | No | MCP protocol version for SSE. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_PROTOCOL_VERSION_HTTP` | No | MCP protocol version for streamable HTTP. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_CLIENT_NAME` | No | MCP client name. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_CLIENT_VERSION` | No | MCP client version. | `mcp_gateway.py:MCPGatewayClient.__init__` |
| `MCP_CLIENT_CAPABILITIES_JSON` | No | JSON capabilities advertised to MCP. | `mcp_gateway.py:MCPGatewayClient._client_capabilities` |

### Python runtime

| Variable | Required | Behavior | Source |
| --- | --- | --- | --- |
| `PYTHONUTF8` | No | Set to `1` at startup. | `main.py` |
| `PYTHONIOENCODING` | No | Set in smoke tests. | `autogenbook/smoke_test.py:_run` |

Complete reference: `docs/CONFIGURATION.md`. (`docs/CONFIGURATION.md`)

## Outputs and artifacts

Common artifacts for all modes:

- `run_meta.json`: Run metadata, configuration, and OpenRouter usage totals. (`autogenbook/llm_usage.py:write_run_meta`)
- `llm_usage.jsonl`: Per-call usage/cost log with modality breakdown. (`autogenbook/llm_usage.py:log_usage`)
- `logs/run.log`: Console + file logging when enabled. (`autogenbook/logging.py:get_logger`, `autogenbook/paths.py:default_run_paths`)

Book/paper structure:

- `structure_graph.json`: Serialized document graph. (`autogenbook/graph/doc_graph.py:save_graph_json`, `autogenbook/state.py:RunContext`)
- `sections/*.md`: Leaf node content (default). Legacy LaTeX uses `sections/*.tex`. (`book_builder.py:generate_contents`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)

Book-specific:

- `context_memory.json`: Running terminology and continuity hints. (`book_builder.py:generate_contents`, `autogenbook/memory/context_memory.py:ContextMemory`)
- `section_reviews/*.json`: Reviewer feedback if enabled. (`book_builder.py:generate_contents`)

Paper-specific:

- `related_work.json`: Literature agent output. (`autogenbook/pipelines/paper_pipeline.py:run_paper`)
- `refs.bib`: BibTeX bibliography (BibTeX mode). (`autogenbook/pipelines/paper_pipeline.py:run_paper`)

Scientist-specific:

- `experiments/<run_id>/metrics.json`, `stdout.log`, `stderr.log`. (`autogenbook/pipelines/scientist_pipeline.py:_run_experiment`)
- `review.json`: Review agent output. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`)

Proposal-specific:

- `outline/`, `draft/`, `final/`: Intermediate and final proposal artifacts. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- `sections/<section_dir>/section.md`, `citations.json`, `used_sources.json`, `tool_calls.json`. (`autogenbook/pipelines/proposal_pipeline.py:_write_section_artifacts`)
- `final/proposal_final.md`: Final proposal. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

Reviewer-specific:

- `review_final.md`: Review output. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)

Audit artifacts:

- `audit_report.json` when audits are enabled. (`autogenbook/audit/latex_auditor.py:audit_latex`)

## Repository layout and customization

- `prompts/<mode>/`: Prompt packs loaded per mode via the loaders in `autogenbook/prompts/*_loader.py`. (`autogenbook/prompts/book_loader.py:load_book_prompts`, `autogenbook/prompts/paper_loader.py:load_paper_prompts`, `autogenbook/prompts/presentation_loader.py:load_presentation_prompts`, `autogenbook/prompts/scientist_loader.py:load_scientist_prompts`, `autogenbook/prompts/proposal_loader.py:load_proposal_prompts`, `autogenbook/prompts/reviewer_loader.py:load_reviewer_prompts`)
- `autogenbook/templates/toy_classification/`: Scientist experiment template cloned into `output/experiments/<run_id>/`. (`autogenbook/pipelines/scientist_pipeline.py:_prepare_run_dir`, `autogenbook/templates/toy_classification/run_experiment.py:main`)
- `preamble/robustness.tex`: LaTeX robustness preamble copied into outputs and included in generated documents. (`utils.py:ensure_robustness_preamble`, `book_builder.py:build_latex_document`, `autogenbook/pipelines/paper_pipeline.py:_build_paper_latex`)
- `input/` and `examples/`: Sample TXT specs and KB inputs used by the example commands in this README. (`input/book/book_input.txt`, `input/paper/paper_input.txt`, `input/proposal/proposal_input.txt`, `examples/book_input.txt`)
- `scripts/check_latex_log.py`: Utility to scan LaTeX logs for common errors. (`scripts/check_latex_log.py:main`)

## Prompt packs (how to modify + what each prompt does)

Prompt packs are plain Markdown files under `prompts/<mode>/` and are loaded at runtime by the mode-specific loaders (`autogenbook/prompts/*_loader.py`). Each loader enumerates required filenames in `DEFAULT_*_PROMPT_FILES`; missing files raise errors on startup. (`autogenbook/prompts/book_loader.py:DEFAULT_BOOK_PROMPT_FILES`, `autogenbook/prompts/paper_loader.py:DEFAULT_PAPER_PROMPT_FILES`, `autogenbook/prompts/presentation_loader.py:DEFAULT_PRESENTATION_PROMPT_FILES`, `autogenbook/prompts/scientist_loader.py:DEFAULT_SCIENTIST_PROMPT_FILES`, `autogenbook/prompts/proposal_loader.py:DEFAULT_PROPOSAL_PROMPT_FILES`, `autogenbook/prompts/reviewer_loader.py:DEFAULT_REVIEWER_PROMPT_FILES`)

How to modify safely:
- Edit the relevant file in `prompts/<mode>/` and keep placeholder tokens. `autogenbook/prompts/agent_prompts.py:render` injects `{GLOBAL_SYSTEM_POLICY}` and `{GLOBAL_EVIDENCE_INSTRUCTIONS}` when referenced. (`autogenbook/prompts/agent_prompts.py:render`)
- Proposal prompts use `{{LANGUAGE}}` placeholders that are replaced in `autogenbook/pipelines/proposal_pipeline.py:_apply_language`. (`autogenbook/pipelines/proposal_pipeline.py:_apply_language`)
- For proposal prompt formatting rules and delimiters, see `prompts/proposal/README.md`. (`prompts/proposal/README.md`)
- After edits, run `python -m autogenbook.smoke_prompts` to check prompt completeness. (`autogenbook/smoke_prompts.py`)

### Book mode prompts (`prompts/book/`)

Markdown-first uses the `_md.md` prompt variants when present (e.g., `book_section_writer_*_md.md`). LaTeX prompts remain available for `--legacy-tex`. (`autogenbook/prompts/book_loader.py:load_book_prompts`)

| Prompt file | Main idea / purpose |
| --- | --- |
| `prompts/book/global_system_policy.md` | Global grounding rules and style constraints for book agents. |
| `prompts/book/global_evidence_instructions.md` | Evidence/citation rules for retrieved excerpts (RID/cite_key). |
| `prompts/book/json_only_system.md` | Force JSON-only output when a JSON response is required. |
| `prompts/book/json_repair_system.md` | Repair invalid JSON output and return only corrected JSON. |
| `prompts/book/mcp_tools_system.md` | Tool-use guidance for MCP paper/search tools. |
| `prompts/book/book_json_from_txt_system.md` | System role for converting a TXT spec into book JSON. |
| `prompts/book/book_json_from_txt_user.md` | User template defining the book JSON schema and input fields. |
| `prompts/book/book_redundancy_system.md` | System role for redundancy review of a book outline JSON. |
| `prompts/book/book_redundancy_user.md` | User task: detect redundant topics and propose a cleaned outline. |
| `prompts/book/structure_subdivider_system.md` | System role for subdividing outline nodes. |
| `prompts/book/structure_subdivider_user.md` | User task: split a parent section into children with page budgets. |
| `prompts/book/book_section_writer_system.md` | System role for book section writing. |
| `prompts/book/book_section_writer_user.md` | User task: write a single book section LaTeX body with grounding. |
| `prompts/book/book_section_reviewer_system.md` | System role for reviewing book sections (grounding/consistency). |
| `prompts/book/book_section_reviewer_user.md` | User task: produce review JSON with issues and required fixes. |
| `prompts/book/book_section_revision_system.md` | System role for revising sections based on review feedback. |
| `prompts/book/book_section_revision_user.md` | User task: apply required fixes to LaTeX body. |
| `prompts/book/context_memory_system.md` | System role for maintaining cross-section terminology memory. |
| `prompts/book/context_memory_user.md` | User task: update context memory JSON from new section text. |
| `prompts/book/length_control_system.md` | System role for length-adjusting LaTeX without changing meaning. |
| `prompts/book/length_control_user.md` | User task: expand/condense a section to target length. |

### Paper mode prompts (`prompts/paper/`)

Markdown-first uses the `_md.md` prompt variants when present (e.g., `paper_section_writer_*_md.md`). LaTeX prompts remain available for `--legacy-tex`. (`autogenbook/prompts/paper_loader.py:load_paper_prompts`)

| Prompt file | Main idea / purpose |
| --- | --- |
| `prompts/paper/global_system_policy.md` | Global grounding rules and style constraints for paper agents. |
| `prompts/paper/global_evidence_instructions.md` | Evidence/citation rules for retrieved excerpts (RID/cite_key). |
| `prompts/paper/json_only_system.md` | Force JSON-only output when a JSON response is required. |
| `prompts/paper/json_repair_system.md` | Repair invalid JSON output and return only corrected JSON. |
| `prompts/paper/mcp_tools_system.md` | Tool-use guidance for MCP paper/search tools. |
| `prompts/paper/paper_json_from_txt_system.md` | System role for converting a TXT spec into paper JSON. |
| `prompts/paper/paper_json_from_txt_user.md` | User template defining the paper JSON schema and input fields. |
| `prompts/paper/structure_subdivider_system.md` | System role for subdividing outline nodes. |
| `prompts/paper/structure_subdivider_user.md` | User task: split a parent section into children with page budgets. |
| `prompts/paper/paper_section_writer_system.md` | System role for paper section writing. |
| `prompts/paper/paper_section_writer_user.md` | User task: write a single paper section LaTeX body with grounding. |
| `prompts/paper/web_literature_system.md` | Literature agent: summarize related work and novelty risks as JSON. |
| `prompts/paper/web_literature_user.md` | User wrapper for the literature agent’s input prompt. |
| `prompts/paper/length_control_system.md` | System role for length-adjusting LaTeX without changing meaning. |
| `prompts/paper/length_control_user.md` | User task: expand/condense a section to target length. |

### Presentation mode prompts (`prompts/presentation/`)

| Prompt file | Purpose |
| --- | --- |
| `prompts/presentation/global_system_policy.md` | Global grounding rules and style constraints for presentation agents. |
| `prompts/presentation/global_evidence_instructions.md` | Evidence/source rules for retrieved excerpts (RID/cite_key). |
| `prompts/presentation/json_only_system.md` | Force JSON-only output when a JSON response is required. |
| `prompts/presentation/json_repair_system.md` | Repair invalid JSON output and return only corrected JSON. |
| `prompts/presentation/mcp_tools_system.md` | Tool-use guidance for MCP paper/search tools. |
| `prompts/presentation/presentation_json_from_txt_system.md` | System role for converting a TXT spec into presentation JSON. |
| `prompts/presentation/presentation_json_from_txt_user.md` | User template defining the presentation JSON schema and inputs. |
| `prompts/presentation/structure_subdivider_system.md` | System role for subdividing slide groups. |
| `prompts/presentation/structure_subdivider_user.md` | User task: split a parent slide group into child slides. |
| `prompts/presentation/presentation_slide_writer_system.md` | System role for slide body writing in Markdown. |
| `prompts/presentation/presentation_slide_writer_user.md` | User task: write a single slide Markdown body with grounding. |
| `prompts/presentation/presentation_narration_system.md` | System role for slide narration generation. |
| `prompts/presentation/presentation_narration_user.md` | User prompt template for slide narration generation. |
| `prompts/presentation/presentation_image_prompt_system.md` | System guidance for slide image prompt generation. |
| `prompts/presentation/presentation_image_prompt_user.md` | Image prompt template using slide text and prior summary. |
| `prompts/presentation/length_control_system.md` | System role for length-adjusting slide Markdown without changing meaning. |
| `prompts/presentation/length_control_user.md` | User task: expand/condense slide body to target length. |

### Scientist mode prompts (`prompts/scientist/`)

| Prompt file | Main idea / purpose |
| --- | --- |
| `prompts/scientist/global_system_policy.md` | Global grounding rules and style constraints for scientist agents. |
| `prompts/scientist/global_evidence_instructions.md` | Evidence/citation rules for retrieved excerpts (RID/cite_key). |
| `prompts/scientist/json_only_system.md` | Force JSON-only output when a JSON response is required. |
| `prompts/scientist/json_repair_system.md` | Repair invalid JSON output and return only corrected JSON. |
| `prompts/scientist/mcp_tools_system.md` | Tool-use guidance for MCP paper/search tools. |
| `prompts/scientist/idea_agent_system.md` | System role for generating candidate research ideas. |
| `prompts/scientist/idea_agent_user.md` | User task: propose multiple testable ideas with hypotheses. |
| `prompts/scientist/literature_agent_system.md` | System role for novelty/related-work analysis. |
| `prompts/scientist/literature_agent_user.md` | User task: assess novelty and build related-work scaffold. |
| `prompts/scientist/plan_agent_system.md` | System role for selecting an idea and building a plan. |
| `prompts/scientist/plan_agent_user.md` | User task: output experiment plan + paper outline JSON. |
| `prompts/scientist/code_patch_agent_system.md` | System role for safe code patch proposals. |
| `prompts/scientist/code_patch_agent_user.md` | User task: propose unified diff to improve the metric. |
| `prompts/scientist/analyze_agent_system.md` | System role for interpreting run artifacts into claims. |
| `prompts/scientist/analyze_agent_user.md` | User task: summarize results and propose figures/tables. |
| `prompts/scientist/review_agent_system.md` | System role for structured peer review of the draft. |
| `prompts/scientist/review_agent_user.md` | User task: produce review JSON with issues and scores. |
| `prompts/scientist/revision_agent_system.md` | System role for applying review-driven revisions. |
| `prompts/scientist/revision_agent_user.md` | User task: revise LaTeX text to address review issues. |
| `prompts/scientist/paper_section_writer_system.md` | System role for LaTeX section writing in scientist mode. |
| `prompts/scientist/paper_section_writer_user.md` | User task: write one paper section LaTeX body. |
| `prompts/scientist/length_control_system.md` | System role for length-adjusting LaTeX without changing meaning. |
| `prompts/scientist/length_control_user.md` | User task: expand/condense a section to target length. |

### Proposal mode prompts (`prompts/proposal/`)

| Prompt file | Main idea / purpose |
| --- | --- |
| `prompts/proposal/llm1_architect_system.md` | LLM1 (architect): define proposal meta-prompt and compliance blueprint. |
| `prompts/proposal/llm2_researcher_outline_system.md` | LLM2 (researcher outline): design proposal structure and plan. |
| `prompts/proposal/llm3_opponent_outline_system.md` | LLM3 (opponent outline): strict review of outline/compliance. |
| `prompts/proposal/llm4_researcher_writer_system.md` | LLM4 (researcher writer): draft full proposal sections. |
| `prompts/proposal/llm5_opponent_final_system.md` | LLM5 (opponent final): final compliance/quality review. |
| `prompts/proposal/metadata_extractor_system.md` | Extract proposal metadata from the input text. |
| `prompts/proposal/json_only_system.md` | Force JSON-only output when a JSON response is required. |
| `prompts/proposal/json_repair_system.md` | Repair invalid JSON output and return only corrected JSON. |
| `prompts/proposal/mcp_tools_system.md` | Tool-use guidance for MCP paper/search tools. |

### Reviewer mode prompts (`prompts/reviewer/`)

| Prompt file | Main idea / purpose |
| --- | --- |
| `prompts/reviewer/prompt1_default.md` | Default LLM1 “architect” prompt when KB1 is unavailable. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`) |
| `prompts/reviewer/architect_prompt.md` | LLM1 instruction to synthesize a final reviewer system prompt from KB1. |
| `prompts/reviewer/prompt2.md` | LLM2 review prompt template (rubric/structure for the review). |
| `prompts/reviewer/reviewer_system.md` | Generic reviewer system guidance (structured, evidence-grounded). |
| `prompts/reviewer/reviewer_user.md` | Generic reviewer user template for review inputs. |

## Retrieval, citations, and audits

- Local KB indexing uses BM25; chunks include stable `rid` and `cite_key` identifiers. (`rag_kb.py:KnowledgeBase`, `rag_kb.py:Chunk`)
- Retrieval items are normalized into `RetrievalItem` objects. (`autogenbook/retrieval/types.py:RetrievalItem`)
- LaTeX citations are extracted and normalized; BibTeX entries are produced from retrieval items when needed. (`autogenbook/citations/extract.py:extract_citations`, `autogenbook/citations/ledger.py:CitationLedger`)
- LaTeX auditing flags unknown citations, missing figures, and numeric claims without evidence. (`autogenbook/audit/latex_auditor.py:audit_latex`)

## Operations & troubleshooting (short)

- If `lualatex` is missing, use `--no-pdf`. (`book_builder.py:compile_pdf`, `main.py:parse_args`)
- Proposal mode requires MCP paper tools; it errors if tools are unavailable. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- Reviewer mode requires a non-empty KB2. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`, `tests/test_reviewer_mode.py`)
- Scan LaTeX logs with `python scripts/check_latex_log.py <file.log>` to catch common errors early. (`scripts/check_latex_log.py:main`)

Full details: `docs/OPERATIONS.md` and `docs/TROUBLESHOOTING.md`. (`docs/OPERATIONS.md`, `docs/TROUBLESHOOTING.md`)

## Developer quickstart

- Install deps: `pip install -r requirements.txt`. (`requirements.txt`)
- Run tests: `python -m unittest`. (`tests/test_reviewer_mode.py`)
- Prompt pack smoke tests: `python -m autogenbook.smoke_prompts`. (`autogenbook/smoke_prompts.py`)
- Schema smoke tests: `python -m autogenbook.schemas.smoke`. (`autogenbook/schemas/smoke.py`)
- End-to-end smoke: `python -m autogenbook.smoke_test` (requires API key unless `AUTOGENBOOK_SMOKE_FAST=1`). (`autogenbook/smoke_test.py`)

More details: `docs/DEVELOPER_GUIDE.md`. (`docs/DEVELOPER_GUIDE.md`)

## Security notes

- Secrets are read from environment variables only. (`openrouter_llm.py:OpenRouterLLM.__init__`, `mcp_gateway.py:MCPGatewayClient.__init__`)
- Retrieved context is sanitized to reduce prompt-injection patterns. (`autogenbook/retrieval/sanitize.py:sanitize_context_text`)
- Scientist patching enforces safety rules for diffs and file paths. (`autogenbook/runner/patch_apply.py:_safety_scan_diff`, `autogenbook/runner/patch_apply.py:_sanitize_patch_path`)

More details: `docs/SECURITY.md`. (`docs/SECURITY.md`)

## Docs map

See `docs/INDEX.md` for the full documentation index. (`docs/INDEX.md`)

## Limitations

- CLI only; no HTTP server is defined in the codebase. (`main.py:parse_args`, `autogenbook/orchestrator.py:run`)
- LLM calls require credentials for the configured endpoint (OpenRouter requires `OPENROUTER_API_KEY`). (`openrouter_llm.py:OpenRouterLLM.__init__`)
- PDF generation requires LuaLaTeX. (`book_builder.py:compile_pdf`)

## License / Contributing

License and contribution guidelines are not defined in runtime code paths; confirm in the repo root before distribution. (Needs confirmation.)
