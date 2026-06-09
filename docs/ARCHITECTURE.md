# Architecture

## High-level overview

The CLI (`main.py`) parses arguments, creates a `RunContext`, and dispatches to a mode-specific pipeline via the orchestrator. (`main.py:parse_args`, `main.py:main`, `autogenbook/state.py:RunContext`, `autogenbook/orchestrator.py:run`)

Each pipeline orchestrates prompt loading, retrieval, section generation, and output assembly. (`autogenbook/pipelines/*`, `autogenbook/prompts/*_loader.py`, `book_builder.py:generate_contents`, `autogenbook/retrieval/manager.py:RetrievalManager`)

## Component breakdown

### CLI and orchestration

- Argument parsing and defaults are defined in `main.py:parse_args`. (`main.py:parse_args`)
- Mode dispatch happens in `autogenbook/orchestrator.py:run`. (`autogenbook/orchestrator.py:run`)

### Pipelines

- Book: `autogenbook/pipelines/book_pipeline.py:run_book`. (`autogenbook/pipelines/book_pipeline.py:run_book`)
- Paper: `autogenbook/pipelines/paper_pipeline.py:run_paper`. (`autogenbook/pipelines/paper_pipeline.py:run_paper`)
- Scientist: `autogenbook/pipelines/scientist_pipeline.py:run_scientist`. (`autogenbook/pipelines/scientist_pipeline.py:run_scientist`)
- Proposal: `autogenbook/pipelines/proposal_pipeline.py:run_proposal`. (`autogenbook/pipelines/proposal_pipeline.py:run_proposal`)
- Reviewer: `autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`. (`autogenbook/pipelines/reviewer_pipeline.py:run_reviewer`)

### Agents and schemas

- Agents build prompts and validate outputs against Pydantic schemas, with optional JSON repair. (`autogenbook/agents/base.py:BaseAgent`, `autogenbook/schemas/*`, `autogenbook/schemas/validate.py`)

### Retrieval and KB

- Local KB uses BM25 over chunked documents. (`rag_kb.py:KnowledgeBase`)
- Retrieval normalizes items into `RetrievalItem` with stable IDs for citations. (`autogenbook/retrieval/manager.py:RetrievalManager`, `autogenbook/retrieval/types.py:RetrievalItem`)
- Web retrieval uses MCP gateway tools and/or Tavily. (`autogenbook/retrieval/mcp_papers.py:MCPPaperRetriever`, `autogenbook/retrieval/tavily.py:TavilyRetriever`, `mcp_gateway.py:MCPGatewayClient`)

### Output assembly

- Book assembly uses `book_builder.build_latex_document`; paper/scientist use `_build_paper_latex`. (`book_builder.py:build_latex_document`, `autogenbook/pipelines/paper_pipeline.py:_build_paper_latex`, `autogenbook/pipelines/scientist_pipeline.py:_build_paper_latex`)
- PDF compilation uses `lualatex` via `book_builder.compile_pdf`. (`book_builder.py:compile_pdf`)
- Markdown export uses `latex2markdown` via `book_builder.export_markdown`. (`book_builder.py:export_markdown`)

### Audit and logging

- LaTeX auditing detects unknown cites, missing figures, and numeric evidence gaps. (`autogenbook/audit/latex_auditor.py:audit_latex`)
- Run metadata and LLM usage are written to `run_meta.json` and `llm_usage.jsonl`. (`autogenbook/llm_usage.py:write_run_meta`, `autogenbook/llm_usage.py:log_usage`)

## Data flow and control flow

### Component diagram

```mermaid
graph TD
  CLI[main.py] --> Orchestrator[autogenbook/orchestrator.py]
  Orchestrator --> Pipelines[autogenbook/pipelines/*]
  Pipelines --> Prompts[prompts/<mode>]
  Pipelines --> LLM[openrouter_llm.py]
  Pipelines --> Retrieval[rag_kb.py + autogenbook/retrieval/*]
  Retrieval --> MCP[mcp_gateway.py]
  Pipelines --> Outputs[out_dir artifacts]
```

Sources: `main.py:main`, `autogenbook/orchestrator.py:run`, `autogenbook/pipelines/*`, `openrouter_llm.py:OpenRouterLLM`, `rag_kb.py:KnowledgeBase`, `autogenbook/retrieval/manager.py:RetrievalManager`, `mcp_gateway.py:MCPGatewayClient`, `autogenbook/state.py:RunContext`

### Sequence diagram (book mode)

```mermaid
sequenceDiagram
  participant User
  participant CLI as main.py
  participant Orchestrator as orchestrator.run
  participant Book as book_pipeline.run_book
  participant KB as KnowledgeBase
  participant LLM as OpenRouterLLM
  participant Builder as book_builder

  User->>CLI: python main.py --mode book
  CLI->>Orchestrator: run(args)
  Orchestrator->>Book: run_book(args)
  Book->>KB: build_from_directory (if kb)
  Book->>LLM: generate_book_json_from_txt
  Book->>Builder: build_graph_from_book_json
  Book->>Builder: subdivide_graph
  loop leaf sections
    Builder->>LLM: BookSectionWriterAgent
    Builder->>LLM: BookSectionReviewerAgent (optional)
    Builder->>LLM: ContextMemoryAgent
  end
  Builder->>Builder: build_latex_document
  Builder->>Builder: compile_pdf / export_markdown
```

Sources: `main.py:main`, `autogenbook/orchestrator.py:run`, `autogenbook/pipelines/book_pipeline.py:run_book`, `rag_kb.py:KnowledgeBase.build_from_directory`, `book_builder.py:generate_book_json_from_txt`, `book_builder.py:build_graph_from_book_json`, `book_builder.py:subdivide_graph`, `book_builder.py:generate_contents`, `autogenbook/agents/*`, `book_builder.py:build_latex_document`, `book_builder.py:compile_pdf`, `book_builder.py:export_markdown`

### Deployment diagram (logical)

```mermaid
graph LR
  Local[Local machine]
  Local -->|HTTP| OpenRouter[OpenRouter API]
  Local -->|HTTP (optional)| MCP[MCP Gateway]
  Local -->|HTTP (optional)| Tavily[Tavily Search]
```

Sources: `openrouter_llm.py:OpenRouterLLM`, `mcp_gateway.py:MCPGatewayClient`, `autogenbook/retrieval/tavily.py:search_web`

## Concurrency model

All pipelines run synchronously and sequentially; there are no explicit threads or async tasks in the core loops. (`autogenbook/pipelines/*`, `book_builder.py:generate_contents`)

Subprocesses are used for LaTeX compilation and for scientist experiments. (`book_builder.py:compile_pdf`, `autogenbook/pipelines/scientist_pipeline.py:_run_experiment`)

## State and persistence

- Run context tracks `run_id`, output paths, and is written to `run_meta.json`. (`autogenbook/state.py:RunContext`, `autogenbook/llm_usage.py:write_run_meta`)
- Book/paper structure graphs are stored as JSON for resume. (`autogenbook/graph/doc_graph.py:save_graph_json`)
- Section files are written under `out_dir/sections/`. (`book_builder.py:generate_contents`, `autogenbook/pipelines/paper_pipeline.py:run_paper`)

## Error handling

Pipelines return non-zero exit codes for missing inputs or audit failures and propagate exceptions for unexpected errors. (`autogenbook/pipelines/book_pipeline.py:run_book`, `autogenbook/pipelines/paper_pipeline.py:run_paper`, `autogenbook/pipelines/proposal_pipeline.py:run_proposal`)

## Extension points

- Add a new mode by creating a pipeline module, registering it in the orchestrator, and adding CLI flags. (`autogenbook/orchestrator.py:run`, `main.py:parse_args`)
- Add or edit prompt packs by editing files under `prompts/<mode>` and loader maps. (`autogenbook/prompts/*_loader.py`, `autogenbook/smoke_prompts.py`)
- Add new schemas for agent outputs under `autogenbook/schemas` and wire them into agents. (`autogenbook/agents/base.py:BaseAgent`, `autogenbook/schemas/*`)
