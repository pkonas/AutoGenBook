# Examples Cookbook

Each example includes a scenario, prerequisites, commands, expected behavior, and common failure modes. File paths are relative to the repo root.

## Example 1: Hello World book (minimal)

Scenario: Generate a small book from a short TXT spec with default settings.

Prerequisites:
- `OPENROUTER_API_KEY` set (or set `AUTOGENBOOK_LLM_BASE_URL` for a local endpoint). (`openrouter_llm.py:OpenRouterLLM.__init__`)

Command:

```bash
python main.py --mode book --input input/book/book_input.txt --out-dir output/book/out_book
```

Expected output/behavior:
- A structure graph snapshot in `output/book/out_book/structure_graph.json`. (`autogenbook/state.py:RunContext`, `autogenbook/graph/doc_graph.py:save_graph_json`)
- Section files under `output/book/out_book/sections/*.tex`. (`book_builder.py:generate_contents`)
- A LaTeX document named from the book title (via `safe_filename`). (`book_builder.py:build_latex_document`, `utils.py:safe_filename`)

Common failure modes:
- Missing input file returns exit code 2. (`autogenbook/pipelines/book_pipeline.py:run_book`)
- Missing `OPENROUTER_API_KEY` raises an error when using OpenRouter. (`openrouter_llm.py:OpenRouterLLM.__init__`)

## Example 2: Resume a book run

Scenario: Resume a partially completed run without regenerating existing sections.

Prerequisites:
- A previous run that created `sections/*.tex` and `structure_graph.json`. (`book_builder.py:generate_contents`, `autogenbook/graph/doc_graph.py:save_graph_json`)

Command:

```bash
python main.py --mode book --input input/book/book_input.txt --out-dir output/book/out_book --resume
```

Expected output/behavior:
- Existing section files are skipped; missing ones are generated. (`book_builder.py:generate_contents`)

Common failure modes:
- If `structure_graph.json` is missing, the pipeline rebuilds the graph. (`autogenbook/pipelines/book_pipeline.py:run_book`)

## Example 3: Book with a local KB

Scenario: Ground book content in local sources.

Prerequisites:
- A directory with supported files (PDF/DOCX/PPTX/MD/TXT). (`rag_kb.py:SUPPORTED_EXTS`)

Command:

```bash
python main.py --mode book --input input/book/book_input.txt --kb-dir input/book --out-dir output/book/out_kb
```

Expected output/behavior:
- KB cache under `output/book/out_kb/.kb_cache`. (`autogenbook/pipelines/book_pipeline.py:run_book`, `rag_kb.py:KnowledgeBase.build_from_directory`)
- `kb_sources.json` with cite key and RID indexes. (`autogenbook/retrieval/kb_citations.py:build_kb_index`)

Common failure modes:
- Invalid KB directory raises a `FileNotFoundError`. (`rag_kb.py:KnowledgeBase.build_from_directory`)

## Example 4: Paper mode with BibTeX citations

Scenario: Generate a paper draft with BibTeX references.

Prerequisites:
- `OPENROUTER_API_KEY` set (or set `AUTOGENBOOK_LLM_BASE_URL` for a local endpoint). (`openrouter_llm.py:OpenRouterLLM.__init__`)

Command:

```bash
python main.py --mode paper --input input/paper/paper_input.txt --citation-style bibtex --out-dir output/paper/out_paper
```

Expected output/behavior:
- `related_work.json` from the literature agent. (`autogenbook/pipelines/paper_pipeline.py:run_paper`)
- `refs.bib` generated from used citations. (`autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/citations/ledger.py:CitationLedger`)

Common failure modes:
- Missing input file returns exit code 2. (`autogenbook/pipelines/paper_pipeline.py:run_paper`)

## Example 5: Paper mode with footnote citations

Scenario: Generate a paper draft that converts KB citations to footnotes.

Prerequisites:
- KB directory (optional but typical for footnote mode). (`rag_kb.py:KnowledgeBase`)

Command:

```bash
python main.py --mode paper --input input/paper/paper_input.txt --citation-style footnote --kb-dir input/paper --out-dir output/paper/out_footnote
```

Expected output/behavior:
- KB citations are rendered as LaTeX footnotes. (`autogenbook/citations/kb_footnotes.py:apply_kb_footnotes`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)

Common failure modes:
- If KB is missing, citations may be incomplete. (`autogenbook/pipelines/paper_pipeline.py:run_paper`)

## Example 6: Scientist mode (toy experiment)

Scenario: Run the built-in toy classification experiment and draft a paper.

Prerequisites:
- `OPENROUTER_API_KEY` set (or set `AUTOGENBOOK_LLM_BASE_URL` for a local endpoint). (`openrouter_llm.py:OpenRouterLLM.__init__`)

Command:

```bash
python main.py --mode scientist --out-dir output/scientist/out_scientist
```

Expected output/behavior:
- Experiment artifacts under `output/scientist/out_scientist/experiments/<run_id>/`. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`)
- `metrics.json` and `figures/accuracy.png` from the template. (`autogenbook/templates/toy_classification/run_experiment.py:main`)
- `review.json` from the review agent. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`)

Common failure modes:
- Missing `OPENROUTER_API_KEY` raises an error when using OpenRouter. (`openrouter_llm.py:OpenRouterLLM.__init__`)

## Example 7: Presentation mode (Markdown + Beamer)

Scenario: Generate a slide deck in Markdown and export to Beamer PDF.

Prerequisites:
- `OPENROUTER_API_KEY` set (or set `AUTOGENBOOK_LLM_BASE_URL` for a local endpoint). (`openrouter_llm.py:OpenRouterLLM.__init__`)
- `pandoc` + `pypandoc` installed for Beamer export.

Command:

```bash
python main.py --mode presentation --input input/presentation/presentation_input.txt --presentation-pptx --presentation-tex --out-dir output/presentation/out_presentation
```

Expected output/behavior:
- `slides/*.md` with per-slide bodies. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
- `images/*.png` with per-slide generated illustrations. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
- A Markdown deck named from the title. (`autogenbook/pipelines/presentation_pipeline.py:run_presentation`)
- A `.pptx` named from the presentation title. (`autogenbook/presentation_export.py:md_to_pptx`)
- A `.tex` and `.pdf` named from the presentation title. (`autogenbook/presentation_export.py:md_to_beamer_tex`)

Common failure modes:
- Missing `python-pptx` prevents PPTX export. (`autogenbook/presentation_export.py:md_to_pptx`)
- Missing `pandoc` or `pypandoc` prevents Beamer export. (`autogenbook/presentation_export.py:_pandoc_convert`)

## Example 8: Proposal mode with KB1/KB2 and MCP tools

Scenario: Generate a grant proposal with MCP-based citations.

Prerequisites:
- `--kb1-dir` and `--kb2-dir` must exist and contain supported files. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `rag_kb.py:SUPPORTED_EXTS`)
- MCP gateway with paper tools must be available. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`, `mcp_gateway.py:MCPGatewayClient`)

Command:

```bash
python main.py --mode proposal --proposal-input input/proposal/proposal_input.txt --kb1-dir input/reviewer/kb1 --kb2-dir input/reviewer/kb2 --enable-web-rag --out-dir output/proposal/out_proposal
```

Expected output/behavior:
- Per-section artifacts under `output/proposal/out_proposal/sections/*/`. (`autogenbook/pipelines/proposal_pipeline.py:_write_section_artifacts`)
- Final proposal at `output/proposal/out_proposal/final/proposal_final.md`. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

Common failure modes:
- Missing KB1/KB2 directories return exit code 2. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- MCP tools unavailable raise a runtime error. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

## Example 9: Reviewer mode (KB2 required)

Scenario: Produce an opponent-style review from a thesis corpus in KB2.

Prerequisites:
- KB2 directory with supported files. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`, `rag_kb.py:SUPPORTED_EXTS`)

Command:

```bash
python main.py --mode reviewer --kb2-dir input/reviewer/kb2 --out-dir output/reviewer/out_reviewer --no-pdf --no-tex
```

Expected output/behavior:
- Review written to `review_final.md` in the output directory. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)

Common failure modes:
- Missing or empty KB2 directory returns exit code 2. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`, `tests/test_reviewer_mode.py`)

## Example 10: Integration pattern (call from Python)

Scenario: Run the orchestrator from your own Python script.

Prerequisites:
- Build an `argparse.Namespace` compatible with `main.py:parse_args`. (`main.py:parse_args`, `autogenbook/orchestrator.py:run`)

Snippet:

```python
from types import SimpleNamespace
from autogenbook.orchestrator import run

args = SimpleNamespace(
    mode="paper",
    input="input/paper/paper_input.txt",
    json_path="paper_structure.json",
    out_dir="output/paper/out_embed",
    kb_dir=None,
    enable_web_rag=False,
    web_rag_k=5,
    no_pdf=True,
    no_md=False,
    resume=False,
    use_json=False,
    use_txt=False,
    citation_style="bibtex",
    paper_venue="arXiv",
    fail_fast_schema=False,
)

exit_code = run(args)
```

Expected output/behavior:
- Same artifacts as CLI for the chosen mode. (`autogenbook/orchestrator.py:run`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)

Common failure modes:
- Missing required fields leads to attribute errors inside the pipeline. (`autogenbook/orchestrator.py:run`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)

## Example 10: LaTeX log gate

Scenario: Fail CI if LaTeX logs contain errors or warnings.

Prerequisites:
- A LaTeX log file produced by `compile_pdf`. (`book_builder.py:compile_pdf`)

Command:

```bash
python scripts/check_latex_log.py output/book/out_book/book.log
```

Expected output/behavior:
- Exit code 0 if no error patterns match; exit code 1 otherwise. (`scripts/check_latex_log.py:main`)

Common failure modes:
- Missing log file returns exit code 2. (`scripts/check_latex_log.py:main`)
